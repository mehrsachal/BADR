"""Google Earth Engine as BADR's data back end.

Earth Engine supplies imagery (optical, SAR), elevation, land cover, night
lights and surface water for anywhere on Earth without the user managing
files.  Products are streamed as tile layers (instant, any size) and can be
downloaded as GeoTIFF when pixel values are needed for analysis.

Requires ``pip install earthengine-api`` and a one-off ``earthengine
authenticate``; set the Cloud project in Settings.  Without it the rest of the
application keeps working (basemaps, open terrain tiles, local files).
"""
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

from ..core.settings import Settings

_ee = None           # the ee module once initialised (tests inject a fake)
_error = ""


def status():
    """-> (ready: bool, message)."""
    if _ee is not None:
        return True, f"Earth Engine ready (project {Settings.get('gee/project') or 'default'})"
    try:
        import ee  # noqa: F401
    except ImportError:
        return False, "earthengine-api is not installed (pip install earthengine-api)"
    if not Settings.get("gee/project"):
        return False, "Set a Google Cloud project for Earth Engine in Settings"
    return False, _error or "Earth Engine not initialised yet"


def init(project=None, force=False):
    global _ee, _error
    if _ee is not None and not force:
        return _ee
    project = project or Settings.get("gee/project")
    try:
        import ee
        key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if key and os.path.exists(key):
            import json
            with open(key) as fh:
                email = json.load(fh).get("client_email")
            ee.Initialize(ee.ServiceAccountCredentials(email, key), project=project or None)
        else:
            ee.Initialize(project=project or None)
        _ee = ee
        _error = ""
    except Exception as exc:
        _error = f"Earth Engine initialisation failed: {exc}"
        raise RuntimeError(_error) from exc
    return _ee


def available():
    if _ee is not None:
        return True
    if not Settings.get("gee/project"):
        return False
    try:
        init()
        return True
    except Exception:
        return False


def _region(ee, bbox):
    west, south, east, north = bbox
    return ee.Geometry.Rectangle([west, south, east, north], None, False)


def _period(start, end, default_days):
    end = end or date.today().isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=default_days)).isoformat()
    return start, end


def _s2(ee, region, start, end, cloud=40):
    def mask(img):
        scl = img.select("SCL")
        ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(ok).divide(10000).copyProperties(img, ["system:time_start"])
    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
            .filterDate(start, end).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud)).map(mask))


def _s1(ee, region, start, end):
    return (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region).filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")).select("VV"))


# product key -> (label, what it is for, default look-back days)
PRODUCTS = {
    "s2_true": ("Sentinel-2 true colour", "recent cloud-free optical picture, 10 m", 60),
    "s2_false": ("Sentinel-2 false colour (vegetation)", "vegetation/concealment in red, 10 m", 60),
    "s2_latest": ("Sentinel-2 most recent scene", "newest single pass, may contain cloud", 20),
    "s1_sar": ("Sentinel-1 radar (all-weather)", "sees through cloud and at night, 10 m", 30),
    "landsat": ("Landsat 8/9 true colour", "30 m optical, long archive", 90),
    "ndvi": ("Vegetation index (NDVI)", "vegetation density for concealment/trafficability", 60),
    "dem": ("Elevation (Copernicus GLO-30)", "30 m terrain heights", 0),
    "landcover": ("Land cover (ESA WorldCover 10 m)", "built-up, crops, forest, water classes", 0),
    "night_lights": ("Night lights (VIIRS)", "settlement activity / electrification", 120),
    "water": ("Surface water occurrence (JRC)", "permanent and seasonal water, river crossing planning", 0),
    "built_up": ("Built-up probability (Dynamic World)", "recent urban extent, 10 m", 90),
}


def build(product, bbox, start=None, end=None):
    """-> (ee.Image, visualisation dict, description)."""
    ee = init()
    if product not in PRODUCTS:
        raise ValueError(f"unknown product {product!r}; available: {sorted(PRODUCTS)}")
    region = _region(ee, bbox)
    start, end = _period(start, end, PRODUCTS[product][2] or 30)
    span = f"{start} to {end}"
    if product == "s2_true":
        return (_s2(ee, region, start, end).median(),
                {"bands": ["B4", "B3", "B2"], "min": 0.02, "max": 0.3, "gamma": 1.2}, span)
    if product == "s2_false":
        return (_s2(ee, region, start, end).median(),
                {"bands": ["B8", "B4", "B3"], "min": 0.02, "max": 0.45}, span)
    if product == "s2_latest":
        col = _s2(ee, region, start, end, cloud=70).sort("system:time_start", False)
        return (col.limit(6).mosaic(), {"bands": ["B4", "B3", "B2"], "min": 0.02, "max": 0.3, "gamma": 1.2}, span)
    if product == "s1_sar":
        return _s1(ee, region, start, end).median(), {"min": -22, "max": -3}, span
    if product == "landsat":
        col = (ee.ImageCollection("LANDSAT/LC09/C02/T1_L2").merge(ee.ImageCollection("LANDSAT/LC08/C02/T1_L2"))
               .filterBounds(region).filterDate(start, end).filter(ee.Filter.lt("CLOUD_COVER", 40)))
        img = col.median().select(["SR_B4", "SR_B3", "SR_B2"]).multiply(0.0000275).add(-0.2)
        return img, {"min": 0.02, "max": 0.3, "gamma": 1.2}, span
    if product == "ndvi":
        img = _s2(ee, region, start, end).median().normalizedDifference(["B8", "B4"])
        return img, {"min": 0, "max": 0.8, "palette": ["#8c510a", "#f6e8c3", "#a6d96a", "#1a9641", "#00441b"]}, span
    if product == "dem":
        img = ee.ImageCollection("COPERNICUS/DEM/GLO30").select("DEM").mosaic()
        return (ee.Terrain.hillshade(img.setDefaultProjection("EPSG:4326", None, 30)),
                {"min": 60, "max": 255}, "static")
    if product == "landcover":
        return ee.ImageCollection("ESA/WorldCover/v200").first(), {"bands": ["Map"]}, "2021"
    if product == "night_lights":
        col = ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG").filterDate(start, end).select("avg_rad")
        return col.median(), {"min": 0, "max": 40, "palette": ["#000000", "#3b2f00", "#ffd000", "#ffffff"]}, span
    if product == "water":
        return (ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence"),
                {"min": 0, "max": 100, "palette": ["#ffffff", "#9ecae1", "#08306b"]}, "1984-2021")
    if product == "built_up":
        col = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region).filterDate(start, end).select("built")
        return col.mean(), {"min": 0.1, "max": 0.7, "palette": ["#00000000", "#ffb000", "#ff0000"]}, span
    raise ValueError(product)


def tile_uri(image, vis):
    """QGIS XYZ data-source URI for an Earth Engine image."""
    map_id = image.getMapId(vis)
    url = map_id["tile_fetcher"].url_format
    return "type=xyz&zmin=0&zmax=20&url=" + urllib.parse.quote(url, safe=":/{}")


def prepare_layer(product, bbox, start=None, end=None, name=None):
    """Network part (worker-thread safe): -> (uri, title, group, summary, props)."""
    image, vis, span = build(product, bbox, start, end)
    title = name or f"{PRODUCTS[product][0]} [{span}]"
    return (tile_uri(image, vis), title, f"Added {PRODUCTS[product][0]} ({span}) from Earth Engine.",
            {"badr/gee_product": product})


def prepare_change(before, after, method="optical", bbox=None):
    """Network part of change detection.

    ``before`` / ``after`` are (start, end) ISO-date pairs.  ``optical`` uses
    the Sentinel-2 vegetation/bare-soil signal (new tracks, clearing, earth
    works, burn scars); ``sar`` uses the Sentinel-1 backscatter log-ratio and
    works through cloud (new structures, vehicles in the open, flooding).
    """
    ee = init()
    region = _region(ee, bbox)
    if method == "sar":
        a = _s1(ee, region, *before).median()
        b = _s1(ee, region, *after).median()
        diff = b.subtract(a)                       # dB difference == log ratio
        vis = {"min": -6, "max": 6, "palette": ["#2166ac", "#00000000", "#b2182b"]}
        label = "SAR change (red: brighter, blue: darker)"
    elif method == "optical":
        a = _s2(ee, region, *before).median().normalizedDifference(["B8", "B4"])
        b = _s2(ee, region, *after).median().normalizedDifference(["B8", "B4"])
        diff = b.subtract(a)
        vis = {"min": -0.35, "max": 0.35, "palette": ["#b2182b", "#00000000", "#1a9850"]}
        label = "Optical change (red: vegetation lost / ground disturbed)"
    else:
        raise ValueError("method must be 'optical' or 'sar'")
    title = f"{label} {before[0]}..{before[1]} vs {after[0]}..{after[1]}"
    return (tile_uri(diff, vis), title,
            f"Change layer added ({method}): {before[0]}..{before[1]} vs {after[0]}..{after[1]}.", {})


def finish_layer(wb, uri, title, summary, props):
    """GUI part: put a prepared Earth Engine tile layer on the map."""
    from qgis.core import QgsRasterLayer
    layer = QgsRasterLayer(uri, title, "wms")
    for k, v in props.items():
        layer.setCustomProperty(k, v)
    wb.add_layer(layer, "Imagery")
    return {"layer": layer.name(), "summary": summary}


def add_layer(wb, product, bbox=None, start=None, end=None, name=None):
    """Stream an Earth Engine product onto the map."""
    out = finish_layer(wb, *prepare_layer(product, bbox or wb.extent_wgs84(), start, end, name))
    out["product"] = product
    return out


def change_detection(wb, before, after, method="optical", bbox=None):
    out = finish_layer(wb, *prepare_change(before, after, method, bbox or wb.extent_wgs84()))
    out["method"] = method
    return out


def download(image, bbox, scale, out_path, progress=None, max_grid=6):
    """Download an image as one GeoTIFF, tiling to stay under EE's request limit."""
    from osgeo import gdal
    ee = init()
    west, south, east, north = bbox
    last_error = None
    for grid in range(1, max_grid + 1):
        paths = []
        try:
            for r in range(grid):
                for c in range(grid):
                    reg = ee.Geometry.Rectangle([
                        west + (east - west) * c / grid, south + (north - south) * r / grid,
                        west + (east - west) * (c + 1) / grid, south + (north - south) * (r + 1) / grid], None, False)
                    url = image.getDownloadURL({"scale": scale, "region": reg, "format": "GEO_TIFF", "crs": "EPSG:4326"})
                    p = f"{out_path}.{grid}_{r}_{c}.tif"
                    urllib.request.urlretrieve(url, p)
                    paths.append(p)
                    if progress:
                        progress("Downloading from Earth Engine", int(100 * len(paths) / (grid * grid)))
            if len(paths) == 1:
                os.replace(paths[0], out_path)
            else:
                vrt = gdal.BuildVRT(out_path + ".vrt", paths)
                gdal.Translate(out_path, vrt, creationOptions=["COMPRESS=DEFLATE"])
                vrt = None
                for p in paths + [out_path + ".vrt"]:
                    os.remove(p)
            return out_path
        except Exception as exc:        # too large: split the request and retry
            last_error = exc
            for p in paths:
                if os.path.exists(p):
                    os.remove(p)
    raise RuntimeError(f"Earth Engine download failed: {last_error}")


def download_dem(bbox, out_path, progress=None):
    ee = init()
    img = ee.ImageCollection("COPERNICUS/DEM/GLO30").select("DEM").mosaic()
    return download(img, bbox, 30, out_path, progress)


def download_product(wb, product, bbox=None, start=None, end=None, scale=10):
    """Download product pixels (not just the picture) for local analysis."""
    from qgis.core import QgsRasterLayer
    bbox = bbox or wb.extent_wgs84()
    image, _vis, span = build(product, bbox, start, end)
    out = download(image, bbox, scale, wb.out_path(f"gee_{product}"))
    layer = QgsRasterLayer(out, f"{PRODUCTS[product][0]} (local) [{span}]")
    wb.add_layer(layer, "Imagery")
    return {"layer": layer.name(), "path": out, "summary": f"Downloaded {PRODUCTS[product][0]} at {scale} m."}
