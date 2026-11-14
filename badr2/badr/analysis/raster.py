"""Raster plumbing shared by the terrain tools (GDAL + numpy, both ship with QGIS)."""
import math
import os

import numpy as np
from osgeo import gdal, osr

from ..core.mgrs import utm_zone

gdal.UseExceptions()


def utm_epsg(lat, lon):
    return (32600 if lat >= 0 else 32700) + utm_zone(lat, lon)


def local_dem(dem_path, lat, lon, radius_m, out_path, max_px=1600):
    """Clip a DEM around a point and reproject to the local UTM zone (metres).

    Terrain maths (slope, visibility, cost) must run in a metric CRS; DEMs
    normally arrive in geographic coordinates, so every tool starts here.
    """
    epsg = utm_epsg(lat, lon)
    src = osr.SpatialReference()
    src.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(epsg)
    x, y, _ = osr.CoordinateTransformation(src, dst).TransformPoint(lon, lat)
    res = max(10.0, 2 * radius_m / max_px)
    ds = gdal.Open(dem_path)
    gt = ds.GetGeoTransform()
    native = abs(gt[1]) * (111320.0 * math.cos(math.radians(lat)) if abs(gt[1]) < 1 else 1.0)
    res = max(res, min(native, 90.0))
    gdal.Warp(out_path, ds, dstSRS=f"EPSG:{epsg}", xRes=res, yRes=res, resampleAlg="bilinear",
              outputBounds=(x - radius_m, y - radius_m, x + radius_m, y + radius_m),
              outputType=gdal.GDT_Float32, dstNodata=-32768, format="GTiff",
              creationOptions=["COMPRESS=DEFLATE"])
    return out_path, epsg, (x, y)


def bbox_dem(dem_path, west, south, east, north, out_path, max_px=1400):
    """Clip a DEM to a WGS84 bbox and reproject to local UTM."""
    lat, lon = (south + north) / 2, (west + east) / 2
    epsg = utm_epsg(lat, lon)
    width_m = (east - west) * 111320.0 * math.cos(math.radians(lat))
    height_m = (north - south) * 110540.0
    res = max(10.0, max(width_m, height_m) / max_px)
    gdal.Warp(out_path, dem_path, dstSRS=f"EPSG:{epsg}", xRes=res, yRes=res, resampleAlg="bilinear",
              outputBounds=(west, south, east, north), outputBoundsSRS="EPSG:4326",
              outputType=gdal.GDT_Float32, dstNodata=-32768, format="GTiff",
              creationOptions=["COMPRESS=DEFLATE"])
    return out_path, epsg


def read(path):
    """-> (array float32 with NaN nodata, geotransform, projection wkt)."""
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype("float32")
    nd = band.GetNoDataValue()
    if nd is not None:
        arr[arr == nd] = np.nan
    arr[arr < -1000] = np.nan
    return arr, ds.GetGeoTransform(), ds.GetProjection()


def write(path, arr, gt, proj, nodata=None, dtype=gdal.GDT_Float32):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(path, arr.shape[1], arr.shape[0], 1, dtype, ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(arr)
    ds.FlushCache()
    ds = None
    return path


def slope_deg(arr, gt):
    """Slope in degrees (Horn-like central differences)."""
    filled = np.where(np.isnan(arr), np.nanmean(arr) if np.isfinite(arr).any() else 0, arr)
    dy, dx = np.gradient(filled, abs(gt[5]), abs(gt[1]))
    s = np.degrees(np.arctan(np.hypot(dx, dy)))
    s[np.isnan(arr)] = np.nan
    return s


def world_to_px(gt, x, y):
    return int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])


def px_to_world(gt, col, row):
    return gt[0] + (col + 0.5) * gt[1], gt[3] + (row + 0.5) * gt[5]


def transformer(src_epsg, dst_epsg):
    a = osr.SpatialReference()
    a.ImportFromEPSG(src_epsg)
    a.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    b = osr.SpatialReference()
    b.ImportFromEPSG(dst_epsg)
    b.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(a, b)


def polygonize(mask, gt, proj, min_area_m2=0):
    """Vectorise a boolean mask -> [(ogr wkt, area_m2)], largest first."""
    from osgeo import ogr
    mem = gdal.GetDriverByName("MEM").Create("", mask.shape[1], mask.shape[0], 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(proj)
    band = mem.GetRasterBand(1)
    band.WriteArray(mask.astype("uint8"))
    drv = ogr.GetDriverByName("Memory")
    vds = drv.CreateDataSource("out")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj)
    lyr = vds.CreateLayer("p", srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(band, band, lyr, 0, [])
    out = []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        a = g.GetArea()
        if a >= min_area_m2:
            out.append((g.Clone(), a))
    out.sort(key=lambda t: -t[1])
    return out


def safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
