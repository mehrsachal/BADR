"""Workbench: the one object UI panels, analysis code and AI tools share.

It owns the QgsProject wiring (layer groups, basemaps), knows where outputs go
and exposes the map view.  It works headless (``canvas=None``) so everything
built on it is testable without a window.
"""
import os
import tempfile

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject,
    QgsRasterLayer, QgsRectangle, QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal

from .store import Operation
from ..symbology.styles import apply_styles, style_areas, style_lines

GROUPS = ["Live", "COP", "Sketch", "Analysis", "Imagery", "Reference", "Basemap"]

BASEMAPS = {
    "OpenStreetMap": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
    "Satellite (Esri World Imagery)": "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
                                      "World_Imagery/MapServer/tile/{z}/{y}/{x}&zmax=19&zmin=0",
    "Topographic (OpenTopoMap)": "type=xyz&url=https://tile.opentopomap.org/{z}/{x}/{y}.png&zmax=17&zmin=0",
}

LAYER_ORDER = {"tracks": "Live", "footprints": "Live", "trails": "Live",
               "detections": "COP", "units": "COP", "incidents": "COP", "lines": "COP", "areas": "COP"}


class Workbench(QObject):
    message = pyqtSignal(str, str)         # text, level (info|warn|error)
    operation_changed = pyqtSignal()

    def __init__(self, canvas=None, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project = QgsProject.instance()
        self.operation = None
        self.wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self._tmp = None

    # ---- operation lifecycle -----------------------------------------
    def open_operation(self, folder=None, name=None):
        if self.operation is not None:
            self.operation.close()             # flush pending edits of the operation being left
        self.project.clear()
        self.project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        root = self.project.layerTreeRoot()
        for g in GROUPS:
            root.addGroup(g)
        default = os.path.basename(os.path.normpath(folder)) if folder else "Scratch operation"
        self.operation = Operation(folder, name or default, parent=self)
        if name:
            self.operation.name = name
        from .settings import Settings
        apply_styles(self.operation, float(Settings.get("ui/symbol_scale")))
        for key, group in LAYER_ORDER.items():
            self.add_layer(self.operation.layer(key), group, top=False)
        if self.operation.user_specs:
            from .exchange import restore_user_layers
            restore_user_layers(self)
        self.operation_changed.emit()
        return self.operation

    @property
    def out_dir(self):
        if self.operation and self.operation.folder:
            d = os.path.join(self.operation.folder, "analysis")
        else:
            if self._tmp is None:
                self._tmp = tempfile.mkdtemp(prefix="badr_")
            d = self._tmp
        os.makedirs(d, exist_ok=True)
        return d

    def out_path(self, stem, ext=".tif"):
        base = os.path.join(self.out_dir, stem)
        path, i = base + ext, 1
        while os.path.exists(path):
            i += 1
            path = f"{base}_{i}{ext}"
        return path

    # ---- layers --------------------------------------------------------
    def add_layer(self, layer, group="Analysis", top=True):
        if layer is None or not layer.isValid():
            raise RuntimeError("layer failed to load")
        self.project.addMapLayer(layer, False)
        node = self.project.layerTreeRoot().findGroup(group) or self.project.layerTreeRoot()
        tree_node = node.insertLayer(0, layer) if top else node.addLayer(layer)
        if tree_node is not None:
            tree_node.setExpanded(False)          # keep the layer list compact; legends open on demand
        return layer

    def set_basemap(self, name):
        group = self.project.layerTreeRoot().findGroup("Basemap")
        for child in list(group.children()):
            self.project.removeMapLayer(child.layerId())
        if name in BASEMAPS:
            layer = QgsRasterLayer(BASEMAPS[name], name, "wms")
            if layer.isValid():
                self.add_layer(layer, "Basemap")
                return True
        return False

    def result_layer(self, kind, title):
        """Styled memory layer for analysis output ('lines' or 'areas' schema)."""
        from .store import SCHEMA
        geom, _t, spec = SCHEMA[kind]
        layer = Operation._memory(geom, title, spec)
        (style_lines if kind == "lines" else style_areas)(layer)
        return self.add_layer(layer, "Analysis")

    def find_layer(self, name):
        if not name:
            return None
        layer = self.project.mapLayer(name)
        if layer:
            return layer
        matches = self.project.mapLayersByName(name)
        if matches:
            return matches[0]
        low = name.lower()
        for lyr in self.project.mapLayers().values():
            if lyr.name().lower() == low:
                return lyr
        for lyr in self.project.mapLayers().values():
            if low in lyr.name().lower():
                return lyr
        return None

    def dem(self):
        """The elevation raster used for terrain analysis, if one is loaded."""
        best = None
        for lyr in self.project.mapLayers().values():
            if not isinstance(lyr, QgsRasterLayer) or lyr.providerType() != "gdal":
                continue
            if lyr.customProperty("badr/role") == "dem":
                return lyr
            n = lyr.name().lower()
            if lyr.bandCount() == 1 and any(t in n for t in ("dem", "dtm", "elev", "srtm", "terrain")):
                best = best or lyr
        return best

    # ---- view ------------------------------------------------------------
    def _to_wgs(self):
        return QgsCoordinateTransform(self.project.crs(), self.wgs84, self.project)

    def _from_wgs(self):
        return QgsCoordinateTransform(self.wgs84, self.project.crs(), self.project)

    def extent_wgs84(self):
        """Current view as (west, south, east, north); None when headless and empty."""
        if self.canvas is not None:
            r = self._to_wgs().transformBoundingBox(self.canvas.extent())
            return (max(r.xMinimum(), -180), max(r.yMinimum(), -85),
                    min(r.xMaximum(), 180), min(r.yMaximum(), 85))
        return getattr(self, "_headless_extent", None)

    def center_wgs84(self):
        ext = self.extent_wgs84()
        if not ext:
            return None
        return ((ext[1] + ext[3]) / 2, (ext[0] + ext[2]) / 2)

    def zoom_to_bbox(self, west, south, east, north, margin=0.15):
        dx, dy = (east - west) * margin, (north - south) * margin
        self._headless_extent = (west - dx, south - dy, east + dx, north + dy)
        if self.canvas is not None:
            rect = QgsRectangle(west - dx, south - dy, east + dx, north + dy)
            self.canvas.setExtent(self._from_wgs().transformBoundingBox(rect))
            self.canvas.refresh()

    def zoom_to(self, lat, lon, radius_m=3000):
        dlat = radius_m / 111320.0
        import math
        dlon = dlat / max(0.05, math.cos(math.radians(lat)))
        self.zoom_to_bbox(lon - dlon, lat - dlat, lon + dlon, lat + dlat, margin=0)

    def zoom_to_layer(self, layer):
        ext = layer.extent()
        if ext.isEmpty() and ext.width() == 0 and ext.height() == 0 and layer.featureCount() != 1:
            return
        tr = QgsCoordinateTransform(layer.crs(), self.wgs84, self.project)
        r = tr.transformBoundingBox(ext)
        if r.width() < 1e-6 and r.height() < 1e-6:
            self.zoom_to(r.center().y(), r.center().x())
        else:
            self.zoom_to_bbox(r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum())

    def refresh(self):
        # canvas.refresh() re-renders only layers that changed (the others come
        # from the render cache); refreshAllLayers() would redraw everything.
        if self.canvas is not None:
            self.canvas.refresh()

    def describe_layers(self):
        """Compact description of non-COP layers for the AI context."""
        from qgis.core import QgsMapLayer
        cop = set(self.operation.layers.values()) if self.operation else set()
        out = []
        for lyr in self.project.mapLayers().values():
            if lyr in cop:
                continue
            if lyr.type() == QgsMapLayer.VectorLayer:
                fields = ", ".join(lyr.fields().names()[:12])
                out.append(f"- vector '{lyr.name()}' {lyr.featureCount()} features, crs {lyr.crs().authid()}, fields: {fields}")
            elif lyr.type() == QgsMapLayer.RasterLayer:
                role = lyr.customProperty("badr/role") or ""
                out.append(f"- raster '{lyr.name()}' {lyr.width()}x{lyr.height()} bands={lyr.bandCount()} "
                           f"crs {lyr.crs().authid()} {('role=' + role) if role else ''}".rstrip())
        return "\n".join(out) or "(no additional layers)"
