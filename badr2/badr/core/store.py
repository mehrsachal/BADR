"""Operation data store: the Common Operational Picture as QGIS layers.

An *operation* is a folder holding one GeoPackage (``cop.gpkg``) with a fixed
set of layers.  Everything the user, the AI or a sensor adds to the picture is
a feature in one of these layers, so it can be styled, queried, analysed by any
of the ~1000 QGIS processing algorithms, and exported to any GIS.

Live sensor tracks are kept in memory layers (they are transient by nature).
"""
import os
import uuid
from datetime import datetime, timezone

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsPointXY, QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QObject, QVariant, pyqtSignal

WGS84 = "EPSG:4326"

S = QVariant.String
D = QVariant.Double
I = QVariant.Int

# layer key -> (geometry, title, [(field, type)])
SCHEMA = {
    "units": ("Point", "Units & Equipment", [
        ("uid", S), ("sidc", S), ("name", S), ("higher", S), ("info", S), ("dtg", S),
        ("heading", D), ("speed", D), ("strength", S), ("source", S), ("review", S),
        ("confidence", D), ("remarks", S)]),
    "incidents": ("Point", "Incidents (SIGACTS)", [
        ("uid", S), ("sidc", S), ("name", S), ("kind", S), ("dtg", S), ("description", S),
        ("casualties", I), ("reliability", S), ("credibility", S), ("source", S),
        ("review", S), ("remarks", S)]),
    "lines": ("LineString", "Control Measures - Lines", [
        ("uid", S), ("kind", S), ("name", S), ("affiliation", S), ("status", S),
        ("source", S), ("review", S), ("remarks", S)]),
    "areas": ("Polygon", "Control Measures - Areas", [
        ("uid", S), ("kind", S), ("name", S), ("affiliation", S), ("status", S),
        ("source", S), ("review", S), ("remarks", S)]),
    "detections": ("Point", "AI Detections (review queue)", [
        ("uid", S), ("label", S), ("sidc", S), ("confidence", D), ("dtg", S),
        ("sensor", S), ("review", S), ("remarks", S)]),
}

LIVE_SCHEMA = {
    "tracks": ("Point", "Live Tracks", [
        ("uid", S), ("sidc", S), ("name", S), ("higher", S), ("info", S), ("dtg", S),
        ("heading", D), ("speed", D), ("alt", D), ("device", S), ("stale", I)]),
    "trails": ("LineString", "Track History", [("uid", S), ("device", S)]),
    "footprints": ("Polygon", "Sensor Footprints", [("uid", S), ("device", S), ("name", S)]),
}


def now_dtg(dt=None):
    """NATO date-time group, Zulu: DDHHMMZMONYY."""
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%d%H%MZ%b%y").upper()


def new_uid():
    return uuid.uuid4().hex[:12]


def _fields(spec):
    f = QgsFields()
    for name, typ in spec:
        f.append(QgsField(name, typ))
    return f


class Operation(QObject):
    """Owns the COP layers and is the single write path into them."""

    changed = pyqtSignal(str)          # layer key
    review_changed = pyqtSignal()

    def __init__(self, folder=None, name="Untitled operation", parent=None):
        super().__init__(parent)
        self.folder = folder
        self.name = name
        self.layers = {}
        self._build()

    # ------------------------------------------------------------------
    @property
    def gpkg(self):
        return os.path.join(self.folder, "cop.gpkg") if self.folder else None

    def _build(self):
        if self.folder:
            os.makedirs(self.folder, exist_ok=True)
        for key, (geom, title, spec) in SCHEMA.items():
            layer = None
            if self.folder:
                layer = self._open_or_create(key, geom, title, spec)
            if layer is None:
                layer = self._memory(geom, title, spec)
            layer.setCustomProperty("badr/key", key)
            self.layers[key] = layer
        for key, (geom, title, spec) in LIVE_SCHEMA.items():
            layer = self._memory(geom, title, spec)
            layer.setCustomProperty("badr/key", key)
            self.layers[key] = layer

    @staticmethod
    def _memory(geom, title, spec):
        layer = QgsVectorLayer(f"{geom}?crs={WGS84}", title, "memory")
        layer.dataProvider().addAttributes(list(_fields(spec)))
        layer.updateFields()
        return layer

    def _open_or_create(self, key, geom, title, spec):
        path = self.gpkg
        uri = f"{path}|layername={key}"
        if os.path.exists(path):
            layer = QgsVectorLayer(uri, title, "ogr")
            if layer.isValid():
                missing = [QgsField(n, t) for n, t in spec if layer.fields().indexOf(n) < 0]
                if missing:
                    layer.dataProvider().addAttributes(missing)
                    layer.updateFields()
                return layer
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = key
        opts.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteLayer if os.path.exists(path)
            else QgsVectorFileWriter.CreateOrOverwriteFile)
        wkb = {"Point": QgsWkbTypes.Point, "LineString": QgsWkbTypes.LineString,
               "Polygon": QgsWkbTypes.Polygon}[geom]
        writer = QgsVectorFileWriter.create(
            path, _fields(spec), wkb, QgsCoordinateReferenceSystem(WGS84),
            QgsProject.instance().transformContext(), opts)
        del writer
        layer = QgsVectorLayer(uri, title, "ogr")
        return layer if layer.isValid() else None

    # ------------------------------------------------------------------
    def layer(self, key):
        return self.layers[key]

    def add(self, key, geometry, attrs=None, source="manual", review=None):
        """Add a feature. ``geometry`` is a QgsGeometry in WGS84.

        Features created by the AI are marked ``review='pending'`` so a human
        confirms them before they count as part of the picture.
        """
        layer = self.layers[key]
        attrs = dict(attrs or {})
        attrs.setdefault("uid", new_uid())
        if layer.fields().indexOf("source") >= 0:
            attrs.setdefault("source", source)
        if layer.fields().indexOf("review") >= 0:
            attrs.setdefault("review", review or ("pending" if source == "ai" else "confirmed"))
        if layer.fields().indexOf("dtg") >= 0:
            attrs.setdefault("dtg", now_dtg())
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geometry)
        for k, v in attrs.items():
            idx = layer.fields().indexOf(k)
            if idx >= 0 and v is not None:
                feat.setAttribute(idx, v)
        ok, added = layer.dataProvider().addFeatures([feat])
        if not ok:
            raise RuntimeError(f"could not add feature to {key}: {layer.dataProvider().lastError()}")
        layer.updateExtents()
        layer.triggerRepaint()
        self.changed.emit(key)
        if attrs.get("review") == "pending":
            self.review_changed.emit()
        return attrs["uid"]

    def add_point(self, key, lat, lon, attrs=None, **kw):
        return self.add(key, QgsGeometry.fromPointXY(QgsPointXY(lon, lat)), attrs, **kw)

    def find(self, key, uid):
        layer = self.layers[key]
        for f in layer.getFeatures(f"\"uid\" = '{uid}'"):
            return f
        return None

    def find_any(self, uid):
        for key in self.layers:
            if self.layers[key].fields().indexOf("uid") < 0:
                continue
            f = self.find(key, uid)
            if f is not None:
                return key, f
        return None, None

    def update(self, key, uid, attrs=None, geometry=None):
        layer = self.layers[key]
        f = self.find(key, uid)
        if f is None:
            return False
        dp = layer.dataProvider()
        if attrs:
            changes = {layer.fields().indexOf(k): v for k, v in attrs.items()
                       if layer.fields().indexOf(k) >= 0}
            dp.changeAttributeValues({f.id(): changes})
        if geometry is not None:
            dp.changeGeometryValues({f.id(): geometry})
        layer.triggerRepaint()
        self.changed.emit(key)
        if attrs and "review" in attrs:
            self.review_changed.emit()
        return True

    def remove(self, key, uid):
        layer = self.layers[key]
        f = self.find(key, uid)
        if f is None:
            return False
        layer.dataProvider().deleteFeatures([f.id()])
        layer.triggerRepaint()
        self.changed.emit(key)
        self.review_changed.emit()
        return True

    def clear(self, key):
        layer = self.layers[key]
        layer.dataProvider().truncate()
        layer.triggerRepaint()
        self.changed.emit(key)

    # ---- review queue (human-in-the-loop) -----------------------------
    def pending(self):
        """All features awaiting human review, as (layer key, feature)."""
        out = []
        for key, layer in self.layers.items():
            if layer.fields().indexOf("review") < 0:
                continue
            for f in layer.getFeatures("\"review\" = 'pending'"):
                out.append((key, f))
        return out

    def resolve(self, key, uid, accept):
        if accept:
            return self.update(key, uid, {"review": "confirmed"})
        if key == "detections":
            return self.update(key, uid, {"review": "rejected"})
        return self.remove(key, uid)

    # ---- summary for the AI context -----------------------------------
    def summary(self, max_items=40):
        lines = []
        for key in SCHEMA:
            layer = self.layers[key]
            n = layer.featureCount()
            if not n:
                continue
            lines.append(f"{SCHEMA[key][1]} [{key}]: {n}")
            for i, f in enumerate(layer.getFeatures()):
                if i >= max_items:
                    lines.append(f"  ... {n - max_items} more")
                    break
                g = f.geometry()
                c = g.centroid().asPoint() if not g.isEmpty() else None
                pos = f"{c.y():.5f},{c.x():.5f}" if c else "?"
                names = layer.fields().names()
                label = f["name"] if "name" in names else f["label"]
                kind = ""
                for fld in ("sidc", "kind"):
                    if fld in names and f[fld]:
                        kind = f"{fld}={f[fld]}"
                        break
                rev = " (PENDING REVIEW)" if "review" in names and f["review"] == "pending" else ""
                lines.append(f"  - uid={f['uid']} {label or ''} {kind} @ {pos}{rev}")
        tracks = self.layers["tracks"].featureCount()
        if tracks:
            lines.append(f"Live tracks: {tracks}")
            for f in self.layers["tracks"].getFeatures():
                p = f.geometry().asPoint()
                lines.append(f"  - {f['name']} via {f['device']} @ {p.y():.5f},{p.x():.5f} "
                             f"hdg={f['heading']} spd={f['speed']} alt={f['alt']}")
        return "\n".join(lines) if lines else "The operational picture is empty."
