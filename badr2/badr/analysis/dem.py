"""Elevation acquisition.

Order of preference: Google Earth Engine (if configured) -> open Terrain Tiles
(Mapzen/Tilezen global DEM hosted on AWS Open Data; no key needed) -> a file
the user already has.  The result is a GeoTIFF tagged as the operation's DEM.
"""
import math
import os
import urllib.request

from osgeo import gdal
from qgis.core import QgsRasterLayer

TERRAIN_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif"
MAX_TILES = 36


def _tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(min(lat, 85.0), -85.0))
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def pick_zoom(west, south, east, north, max_tiles=MAX_TILES, max_zoom=13):
    for z in range(max_zoom, 4, -1):
        x0, y0 = _tile(north, west, z)
        x1, y1 = _tile(south, east, z)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return z
    return 5


def fetch_terrain_tiles(bbox, out_path, progress=None, opener=urllib.request.urlopen):
    """Download and mosaic open terrain tiles covering ``bbox`` (W,S,E,N)."""
    west, south, east, north = bbox
    z = pick_zoom(west, south, east, north)
    x0, y0 = _tile(north, west, z)
    x1, y1 = _tile(south, east, z)
    tiles = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    work = out_path + "_tiles"
    os.makedirs(work, exist_ok=True)
    paths = []
    for i, (x, y) in enumerate(tiles):
        p = os.path.join(work, f"{z}_{x}_{y}.tif")
        if not os.path.exists(p):
            req = urllib.request.Request(TERRAIN_TILES.format(z=z, x=x, y=y),
                                         headers={"User-Agent": "badr-gis"})
            with opener(req, timeout=60) as resp, open(p, "wb") as fh:
                fh.write(resp.read())
        paths.append(p)
        if progress:
            progress("Downloading elevation", int(100 * (i + 1) / len(tiles)))
    vrt = gdal.BuildVRT(os.path.join(work, "mosaic.vrt"), paths)
    gdal.Warp(out_path, vrt, dstSRS="EPSG:4326", outputBounds=(west, south, east, north),
              resampleAlg="bilinear", format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    vrt = None
    return out_path


def register_dem(wb, path, name="Elevation (DEM)"):
    """Load a DEM file and mark it as the layer terrain tools should use."""
    for lyr in wb.project.mapLayers().values():
        if lyr.customProperty("badr/role") == "dem":
            lyr.removeCustomProperty("badr/role")
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        raise RuntimeError(f"not a readable raster: {path}")
    layer.setCustomProperty("badr/role", "dem")
    wb.add_layer(layer, "Reference")
    node = wb.project.layerTreeRoot().findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(False)       # data layer, not a picture
    return layer


def fetch_elevation(bbox, out, progress=None):
    """Network part (safe on a worker thread): download a DEM to ``out``; returns the source name."""
    west, south, east, north = bbox
    if (east - west) > 3 or (north - south) > 3:
        raise ValueError("area too large for a planning DEM; zoom in below about 300 km across")
    try:
        from ..gee import client as gee
        if gee.available():
            gee.download_dem(bbox, out, progress=progress)
            return "Google Earth Engine (Copernicus GLO-30)"
    except Exception:
        pass
    fetch_terrain_tiles(bbox, out, progress)
    return "open terrain tiles"


def get_elevation(wb, bbox=None, progress=None):
    """Fetch a DEM for the bbox (default: current view) and register it."""
    bbox = bbox or wb.extent_wgs84()
    if not bbox:
        raise ValueError("no area given")
    out = wb.out_path("dem")
    source = fetch_elevation(bbox, out, progress)
    layer = register_dem(wb, out)
    return {"layer": layer.name(), "source": source, "path": out,
            "summary": f"Elevation loaded from {source} ({layer.width()}x{layer.height()} px)."}
