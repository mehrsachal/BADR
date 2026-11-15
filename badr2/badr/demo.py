"""Fictional training scenario (EXERCISE IRON SENTINEL).

Everything here is invented for demonstration and laid over a public training
area (the U.S. National Training Center, Fort Irwin, California).  It is not
derived from any real operation.
"""
import os
import random

from qgis.core import QgsGeometry, QgsPointXY, QgsRasterLayer

from .core import geodesy
from .symbology import sidc as S

CENTER = (35.262, -116.684)


def _at(bearing, dist_m):
    return geodesy.destination(CENTER[0], CENTER[1], bearing, dist_m)


def _line(*pts):
    return QgsGeometry.fromPolylineXY([QgsPointXY(lon, lat) for lat, lon in pts])


def _poly(*pts):
    ring = [QgsPointXY(lon, lat) for lat, lon in pts]
    return QgsGeometry.fromPolygonXY([ring + [ring[0]]])


def _circle(lat, lon, r):
    return QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in geodesy.circle(lat, lon, r)]])


def load_demo(win, with_terrain=True, live_feed=True):
    wb, op = win.wb, win.wb.operation
    op.name = "EX IRON SENTINEL (fictional)"
    win.opname.setText(op.name)
    rnd = random.Random(7)

    def unit(bearing, dist, symbol_set, entity, aff, name="", echelon="none", source="manual", **kw):
        lat, lon = _at(bearing, dist)
        code = S.build(symbol_set, entity, aff, echelon=echelon, **{k: v for k, v in kw.items()
                                                                    if k in ("hq", "mod1", "mod2", "status")})
        return op.add_point("units", lat, lon, {"sidc": code, "name": name, "higher": kw.get("higher", "")},
                            source=source)

    # friendly
    unit(250, 5200, "10", "110000", "friend", "TF IRON", "battalion", hq="hq")
    unit(245, 4300, "10", "121100", "friend", "A", "company", higher="1-5")
    unit(200, 2600, "10", "121300", "friend", "SCT", "platoon", higher="1-5")
    unit(262, 4700, "10", "140800", "friend", "EOD 2", "team")
    unit(232, 5600, "10", "130800", "friend", "MTR", "section", higher="1-5")
    unit(150, 3900, "10", "200700", "assumed_friend", "DIST POLICE", "platoon")
    unit(215, 3300, "25", "130300", "friend", "CP 1")
    unit(300, 2100, "25", "160100", "friend", "OP 1")
    # threat
    unit(20, 900, "20", "111700", "hostile", "OBJ FOX")
    unit(35, 1500, "11", "110400", "hostile", "CELL 3", mod1="19")
    unit(60, 2500, "20", "110300", "suspect", "CACHE?")
    unit(350, 3200, "10", "130800", "hostile", "", "team", status="planned")
    unit(110, 1900, "15", "160800", "hostile", "WHITE PICKUP")
    # human terrain
    unit(95, 700, "11", "110400", "neutral", "KHAR VILLAGE")
    unit(120, 950, "20", "120402", "neutral", "SCHOOL")
    unit(80, 1200, "20", "120702", "neutral", "CLINIC")
    unit(140, 500, "20", "121004", "neutral", "")

    # control measures
    msr = [_at(248, 5600), _at(240, 4200), _at(225, 2900), _at(200, 1700), _at(150, 1100), _at(100, 1500),
           _at(80, 2900)]
    alt = [_at(248, 5600), _at(270, 4300), _at(295, 3000), _at(330, 2200), _at(10, 2100), _at(45, 2700)]
    op.add("lines", _line(*msr), {"kind": "route", "name": "IRON"})
    op.add("lines", _line(*alt), {"kind": "route", "name": "COPPER"})
    op.add("lines", _line(_at(300, 4200), _at(240, 2400), _at(185, 3600)), {"kind": "phase_line", "name": "RED"})
    op.add("lines", _line(_at(240, 4000), _at(232, 2900), _at(215, 1700)), {"kind": "axis_of_advance", "name": "",
                                                                             "status": "planned"})
    op.add("lines", _line(_at(30, 4200), _at(32, 2600), _at(25, 1200)), {"kind": "threat_route", "name": "",
                                                                         "affiliation": "hostile"})
    op.add("areas", _circle(*_at(250, 5200), 650), {"kind": "base", "name": "IRON"})
    op.add("areas", _poly(_at(350, 1900), _at(50, 2100), _at(75, 900), _at(330, 600)), {"kind": "nai", "name": "1"})
    fox = _at(20, 900)
    op.add("areas", _circle(*fox, 180), {"kind": "cordon_inner", "name": "", "status": "planned"})
    op.add("areas", _circle(*fox, 520), {"kind": "cordon_outer", "name": "", "status": "planned"})
    op.add("areas", _circle(*_at(60, 2500), 420), {"kind": "guerrilla_base", "name": "", "affiliation": "hostile"})

    # incidents: an evening IED cluster on RTE IRON plus scattered activity
    def incident(lat, lon, entity, kind, day, hour, cas=0, **kw):
        code = S.build("40", entity, kw.get("aff", "hostile"), mod1=kw.get("mod1", "00"))
        op.add_point("incidents", lat, lon, {"sidc": code, "kind": kind, "name": "",
                                             "dtg": f"{day:02d}{hour:02d}{rnd.randrange(60):02d}ZNOV26",
                                             "casualties": cas, "reliability": "B", "credibility": "2"})
    hot = _at(196, 1650)
    for i in range(7):
        la, lo = geodesy.destination(hot[0], hot[1], rnd.uniform(0, 360), rnd.uniform(30, 260))
        incident(la, lo, "110301" if i % 3 else "110300", "IED explosion" if i % 3 else "IED found & cleared",
                 2 + i, rnd.choice([17, 18, 18, 19]), cas=rnd.choice([0, 1, 2]), mod1="00" if i % 3 else "13")
    for i in range(4):
        la, lo = geodesy.destination(*_at(95, 1500), rnd.uniform(0, 360), rnd.uniform(50, 300))
        incident(la, lo, "110400", "Shooting / small-arms fire", 4 + 2 * i, rnd.choice([5, 6, 21]))
    incident(*_at(125, 1000), "110100", "Kidnapping", 9, 2, mod1="05")
    incident(*_at(300, 2600), "110401", "Sniping", 11, 6, cas=1)
    incident(*_at(100, 800), "120100", "Demonstration", 12, 10, aff="neutral")
    incident(*_at(70, 2300), "110604", "Mortar / indirect fire", 13, 20)

    # AI-originated items awaiting human review (what the review queue is for)
    la, lo = _at(48, 1900)
    op.add_point("units", la, lo, {"sidc": S.build("20", "110300", "suspect"), "name": "CACHE (AI)",
                                   "remarks": "Suggested from new vehicle tracks in change layer"}, source="ai")
    la, lo = _at(8, 2700)
    op.add_point("detections", la, lo, {"sidc": S.build("15", "160201", "unknown"), "label": "pickup x2",
                                        "confidence": 0.71, "sensor": "RAVEN-7"}, source="ai", review="pending")

    wb.zoom_to(CENTER[0] + 0.004, CENTER[1] - 0.012, 5200)

    if with_terrain:
        try:
            _terrain(win)
        except Exception as exc:              # offline: the scenario still loads
            win.say(f"Demo terrain not loaded ({exc}).", "warn")
    from .analysis import pattern
    pattern.hotspots(wb, 400, 3)
    if live_feed:
        win.toggle_simulator()
    win._sync_layers()
    win.say("Fictional training scenario loaded. Try: \"which route is more exposed to IEDs, and what can OP 1 see?\"")


def _terrain(win):
    """Elevation from open terrain tiles + a hillshade backdrop + two terrain products."""
    from osgeo import gdal
    from .analysis import dem, terrain
    wb = win.wb
    bbox = (CENTER[1] - 0.11, CENTER[0] - 0.075, CENTER[1] + 0.085, CENTER[0] + 0.08)
    path = os.path.join(wb.out_dir, "demo_dem.tif")
    if not os.path.exists(path):
        dem.fetch_terrain_tiles(bbox, path)
    dem.register_dem(wb, path)
    shade = os.path.join(wb.out_dir, "demo_hillshade.tif")
    if not os.path.exists(shade):
        gdal.DEMProcessing(shade, path, "hillshade", scale=111120, zFactor=2.0, azimuth=315, altitude=40)
    layer = QgsRasterLayer(shade, "Hillshade (local)")
    layer.renderer().setOpacity(1.0)
    wb.add_layer(layer, "Basemap")
    op1 = _at(300, 2100)
    terrain.viewshed(wb, op1[0], op1[1], observer_height=3, radius_m=3500, name="Viewshed OP 1")
