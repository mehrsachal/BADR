"""OpenStreetMap reference data for the area of operations (via Overpass)."""
import json
import urllib.parse
import urllib.request

from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant

OVERPASS = "https://overpass-api.de/api/interpreter"

# theme -> (overpass filter, geometry, QGIS style)
THEMES = {
    "roads": ('way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|track"]',
              "LineString", {"color": "#f2e9d0", "width": "0.5"}),
    "waterways": ('way["waterway"~"river|stream|canal"]', "LineString", {"color": "#4aa3df", "width": "0.6"}),
    "bridges": ('way["bridge"="yes"]["highway"]', "LineString", {"color": "#ff9d00", "width": "1.2"}),
    "buildings": ('way["building"]', "Polygon", {"color": "200,200,200,70", "outline_color": "#bdbdbd"}),
    "places": ('node["place"~"city|town|village|hamlet"]', "Point", {"color": "#ffffff", "size": "2"}),
    "health": ('nwr["amenity"~"hospital|clinic"]', "Point", {"color": "#ff5c5c", "size": "2.5"}),
    "schools": ('nwr["amenity"~"school|college|university"]', "Point", {"color": "#ffd24a", "size": "2.5"}),
    "worship": ('nwr["amenity"="place_of_worship"]', "Point", {"color": "#c792ea", "size": "2.5"}),
    "government": ('nwr["office"="government"]', "Point", {"color": "#7fd1ff", "size": "2.5"}),
    "police": ('nwr["amenity"="police"]', "Point", {"color": "#5c9dff", "size": "2.5"}),
    "fuel": ('nwr["amenity"="fuel"]', "Point", {"color": "#ff9d00", "size": "2.5"}),
    "towers": ('nwr["man_made"~"tower|mast"]', "Point", {"color": "#e0e0e0", "size": "2"}),
}


def _query(theme, bbox):
    west, south, east, north = bbox
    flt = THEMES[theme][0]
    return f"[out:json][timeout:60];({flt}({south},{west},{north},{east}););out geom tags center 4000;"


def fetch(theme, bbox, opener=urllib.request.urlopen):
    data = urllib.parse.urlencode({"data": _query(theme, bbox)}).encode()
    req = urllib.request.Request(OVERPASS, data=data, headers={"User-Agent": "badr-gis"})
    with opener(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_layer(theme, payload):
    _flt, geom, _style = THEMES[theme]
    layer = QgsVectorLayer(f"{geom}?crs=EPSG:4326", f"OSM {theme}", "memory")
    layer.dataProvider().addAttributes([QgsField("osm_id", QVariant.String), QgsField("name", QVariant.String),
                                        QgsField("type", QVariant.String)])
    layer.updateFields()
    feats = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        g = None
        if geom == "Point":
            if el["type"] == "node":
                g = QgsGeometry.fromPointXY(QgsPointXY(el["lon"], el["lat"]))
            elif "center" in el:
                g = QgsGeometry.fromPointXY(QgsPointXY(el["center"]["lon"], el["center"]["lat"]))
        elif "geometry" in el:
            pts = [QgsPointXY(p["lon"], p["lat"]) for p in el["geometry"]]
            if geom == "LineString" and len(pts) >= 2:
                g = QgsGeometry.fromPolylineXY(pts)
            elif geom == "Polygon" and len(pts) >= 4:
                g = QgsGeometry.fromPolygonXY([pts])
        if g is None:
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(g)
        f["osm_id"] = f"{el['type']}/{el['id']}"
        f["name"] = tags.get("name:en") or tags.get("name") or ""
        f["type"] = (tags.get("highway") or tags.get("waterway") or tags.get("amenity") or tags.get("place")
                     or tags.get("building") or tags.get("man_made") or tags.get("office") or "")
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def fetch_checked(theme, bbox, opener=urllib.request.urlopen):
    """Network part (worker-thread safe)."""
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}; available: {sorted(THEMES)}")
    if not bbox or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.5:
        raise ValueError("area too large for an OSM import; zoom in below about 50 km across")
    return fetch(theme, bbox, opener)


def load_theme(wb, theme, payload):
    """GUI part: build and style the layer from a fetched payload."""
    from qgis.core import QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer
    layer = to_layer(theme, payload)
    geom, style = THEMES[theme][1], THEMES[theme][2]
    sym = {"Point": QgsMarkerSymbol, "LineString": QgsLineSymbol, "Polygon": QgsFillSymbol}[geom].createSimple(style)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    wb.add_layer(layer, "Reference")
    return {"layer": layer.name(), "features": layer.featureCount(),
            "summary": f"Imported {layer.featureCount()} OSM {theme} features."}


def import_theme(wb, theme, bbox=None, opener=urllib.request.urlopen):
    return load_theme(wb, theme, fetch_checked(theme, bbox or wb.extent_wgs84(), opener))
