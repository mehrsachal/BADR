"""Getting data out of (and custom layers into) BADR in standard GIS formats."""
import os
import re
import shutil

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsField, QgsFillSymbol, QgsLineSymbol, QgsMapLayer, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsProject, QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorFileWriter, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

# label -> (OGR driver, extension, one file can hold several layers)
FORMATS = {
    "GeoPackage (.gpkg)": ("GPKG", ".gpkg", True),
    "Shapefile (.shp)": ("ESRI Shapefile", ".shp", False),
    "GeoJSON (.geojson)": ("GeoJSON", ".geojson", False),
    "KML - Google Earth / ATAK (.kml)": ("KML", ".kml", False),
    "CSV with coordinates (.csv)": ("CSV", ".csv", False),
    "File geodatabase - ArcGIS (.gdb)": ("OpenFileGDB", ".gdb", True),
}
ALIASES = {"gpkg": "GeoPackage (.gpkg)", "geopackage": "GeoPackage (.gpkg)", "shp": "Shapefile (.shp)",
           "shapefile": "Shapefile (.shp)", "geojson": "GeoJSON (.geojson)", "json": "GeoJSON (.geojson)",
           "kml": "KML - Google Earth / ATAK (.kml)", "csv": "CSV with coordinates (.csv)",
           "gdb": "File geodatabase - ArcGIS (.gdb)", "arcgis": "File geodatabase - ArcGIS (.gdb)"}


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "layer"


def format_key(name):
    if name in FORMATS:
        return name
    key = ALIASES.get(str(name).lower().lstrip("."))
    if not key:
        raise ValueError(f"unknown format {name!r}; use one of: gpkg, shp, geojson, kml, csv, gdb")
    return key


def exportable_layers(project=None):
    """Vector layers with features, and file-based rasters."""
    out = []
    for lyr in (project or QgsProject.instance()).mapLayers().values():
        if lyr.type() == QgsMapLayer.VectorLayer and lyr.featureCount() > 0:
            out.append(lyr)
        elif lyr.type() == QgsMapLayer.RasterLayer and lyr.providerType() == "gdal" and os.path.exists(lyr.source()):
            out.append(lyr)
    return out


def export_layers(layers, folder, fmt="GeoPackage (.gpkg)", name="badr_export", selected_only=False):
    """Write layers to ``folder``. Returns the list of files written.

    Multi-layer formats get one file ``<name>.<ext>`` with a table per layer;
    single-layer formats get one file per layer.  Rasters are copied as GeoTIFF.
    Everything is written in WGS84 so any GIS can read it without guessing.
    """
    fmt = format_key(fmt)
    driver, ext, multi = FORMATS[fmt]
    os.makedirs(folder, exist_ok=True)
    written, used = [], set()
    container = os.path.join(folder, _slug(name) + ext)
    if multi and os.path.exists(container):
        shutil.rmtree(container) if os.path.isdir(container) else os.remove(container)
    ctx = QgsProject.instance().transformContext()
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    first = True
    for lyr in layers:
        if lyr.type() == QgsMapLayer.RasterLayer:
            dst = os.path.join(folder, _slug(lyr.name()) + ".tif")
            if os.path.abspath(dst) != os.path.abspath(lyr.source()):
                shutil.copyfile(lyr.source(), dst)
            written.append(dst)
            continue
        if lyr.type() != QgsMapLayer.VectorLayer or lyr.featureCount() == 0:
            continue
        table = _slug(lyr.name())
        while table in used:
            table += "_2"
        used.add(table)
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = driver
        opts.layerName = table
        opts.fileEncoding = "UTF-8"
        opts.onlySelectedFeatures = bool(selected_only and lyr.selectedFeatureCount())
        from qgis.core import QgsCoordinateTransform
        opts.ct = QgsCoordinateTransform(lyr.crs(), wgs, QgsProject.instance())
        if driver == "CSV":
            opts.layerOptions = ["GEOMETRY=AS_WKT" if lyr.geometryType() != 0 else "GEOMETRY=AS_XY"]
        if driver == "KML":
            opts.datasourceOptions = ["NameField=name"]
        if multi:
            path = container
            opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                         else QgsVectorFileWriter.CreateOrOverwriteLayer)
        else:
            path = os.path.join(folder, table + ext)
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, path, ctx, opts)
        if res[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"export of '{lyr.name()}' failed: {res[1]}")
        first = False
        if path not in written:
            written.append(path)
    if not written:
        raise ValueError("nothing to export: the chosen layers are empty")
    return written


# ---------------------------------------------------------------------------
# User-defined (non-NATO) layers
# ---------------------------------------------------------------------------
GEOMS = {"point": "Point", "line": "LineString", "polygon": "Polygon"}


def style_user_layer(layer, color="#ffd24a"):
    c = QColor(color)
    gt = layer.geometryType()
    if gt == 0:
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": c.name(), "size": "3.2",
                                            "outline_color": "#101214", "outline_width": "0.4"})
    elif gt == 1:
        sym = QgsLineSymbol.createSimple({"color": c.name(), "width": "0.9"})
    else:
        sym = QgsFillSymbol.createSimple({"color": f"{c.red()},{c.green()},{c.blue()},45",
                                          "outline_color": c.name(), "outline_width": "0.8"})
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    fmt = QgsTextFormat()
    fmt.setSize(9)
    fmt.setColor(QColor("#ffffff"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(0, 0, 0, 210))
    fmt.setBuffer(buf)
    pal = QgsPalLayerSettings()
    pal.fieldName = "name"
    pal.setFormat(fmt)
    if gt == 1:
        pal.placement = QgsPalLayerSettings.Line
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.setCustomProperty("badr/color", c.name())
    layer.triggerRepaint()


def create_user_layer(wb, name, geometry="point", color="#ffd24a", fields=()):
    """A free-form layer for anything that is not a NATO symbol.

    Saved in the operation's GeoPackage when the operation has a folder,
    otherwise held in memory.  Always has ``name`` and ``notes`` text fields.
    """
    geom = GEOMS.get(str(geometry).lower())
    if not geom:
        raise ValueError("geometry must be point, line or polygon")
    name = (name or "").strip()
    if not name:
        raise ValueError("the layer needs a name")
    spec = [QgsField("name", QVariant.String), QgsField("notes", QVariant.String)]
    spec += [QgsField(_slug(f), QVariant.String) for f in fields if _slug(f) not in ("name", "notes")]
    layer = QgsVectorLayer(f"{geom}?crs=EPSG:4326", name, "memory")
    layer.dataProvider().addAttributes(spec)
    layer.updateFields()
    layer.setCustomProperty("badr/user", True)
    style_user_layer(layer, color)
    wb.add_layer(layer, "Sketch")
    if wb.operation is not None:
        wb.operation.register_user_layer(layer)      # saved with the operation from now on
    return layer


def restore_user_layers(wb):
    """Recreate the user layers recorded in a re-opened operation."""
    op = wb.operation
    for spec in list(op.user_specs):
        src = QgsVectorLayer(f"{op.gpkg}|layername={spec['table']}", spec["name"], "ogr")
        if not src.isValid():
            continue
        geom = {0: "point", 1: "line", 2: "polygon"}.get(src.geometryType())
        if geom is None:
            continue
        extra = [n for n in src.fields().names() if n not in ("fid", "name", "notes")]
        del src
        layer = create_user_layer(wb, spec["name"], geom, spec.get("color", "#ffd24a"), extra)
        op._load_into(layer, spec["table"])
        layer.triggerRepaint()
    op.user_specs = []
    op._dirty = False
    op._timer.stop()


def user_layers(wb):
    return [l for l in wb.project.mapLayers().values() if l.customProperty("badr/user")]


def editable_layers(wb):
    """Layers the Draw mode can add features to: quick layers and catalog feature classes."""
    out = []
    for l in wb.project.mapLayers().values():
        if l.type() == QgsMapLayer.VectorLayer and l.geometryType() in (0, 1, 2) and (
                l.customProperty("badr/user") or l.customProperty("badr/editable")):
            out.append(l)
    return out


def add_user_feature(wb, layer, latlons, name="", notes="", attrs=None):
    """Add a feature from [(lat, lon), ...] (WGS84) to a quick layer or feature class."""
    from qgis.core import QgsCoordinateTransform, QgsFeature, QgsGeometry, QgsPointXY, QgsWkbTypes
    pts = [QgsPointXY(lon, lat) for lat, lon in latlons]
    gt = layer.geometryType()
    if gt == 0:
        geom = QgsGeometry.fromPointXY(pts[0])
    elif gt == 1:
        if len(pts) < 2:
            raise ValueError("a line needs at least 2 points")
        geom = QgsGeometry.fromPolylineXY(pts)
    else:
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 points")
        geom = QgsGeometry.fromPolygonXY([pts + [pts[0]]])
    if layer.crs().isValid() and layer.crs().authid() != "EPSG:4326":
        geom.transform(QgsCoordinateTransform(wb.wgs84, layer.crs(), wb.project))
    if QgsWkbTypes.isMultiType(layer.wkbType()):
        geom.convertToMultiType()
    f = QgsFeature(layer.fields())
    f.setGeometry(geom)
    values = {"name": name, "notes": notes}
    values.update(attrs or {})
    for key, value in values.items():
        idx = layer.fields().indexOf(key)
        if idx >= 0 and value not in (None, ""):
            f.setAttribute(idx, value)
    ok, _ = layer.dataProvider().addFeatures([f])
    if not ok:
        raise RuntimeError(f"could not add the feature: {layer.dataProvider().lastError()}")
    layer.updateExtents()
    layer.triggerRepaint()
    if wb.operation is not None and layer.customProperty("badr/user"):
        wb.operation.touch()
    return True
