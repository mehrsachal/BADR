"""Operation data store: the Common Operational Picture as QGIS layers.

An *operation* is a folder holding one GeoPackage (``cop.gpkg``) with a fixed
set of layers.  Everything the user, the AI or a sensor adds to the picture is
a feature in one of these layers, so it can be styled, queried, analysed by any
QGIS processing algorithm, and exported to any GIS.

Live sensor tracks are kept in memory layers (they are transient by nature).
"""
import os
import uuid
from datetime import datetime, timezone

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsPointXY, QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QObject, QTimer, QVariant, pyqtSignal

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
    """Owns the COP layers and is the single write path into them.

    The layers live in memory - edits, live tracks and map redraws never wait
    on disk - and are written to the operation's GeoPackage by a debounced
    autosave (and on close).  Readers and the writer therefore never contend
    for the database file while the map is rendering in background threads.
    """

    changed = pyqtSignal(str)          # layer key
    review_changed = pyqtSignal()
    saved = pyqtSignal()

    AUTOSAVE_MS = 1500

    def __init__(self, folder=None, name="Untitled operation", parent=None):
        super().__init__(parent)
        self.folder = folder
        self.name = name
        self.layers = {}
        self.user_layers = []          # free-form layers saved with the operation
        self.user_specs = []           # [{table, name, color}] found on disk, to be restored
        self.data_layers = []          # [[geodatabase path, feature class]] shown on the map
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.AUTOSAVE_MS)
        self._timer.timeout.connect(self._autosave)
        self._build()
        self.changed.connect(self._on_changed)

    # ------------------------------------------------------------------
    @property
    def gpkg(self):
        return os.path.join(self.folder, "cop.gpkg") if self.folder else None

    @property
    def meta_path(self):
        return os.path.join(self.folder, "operation.json") if self.folder else None

    def _build(self):
        if self.folder:
            os.makedirs(self.folder, exist_ok=True)
            self._read_meta()
        for key, (geom, title, spec) in SCHEMA.items():
            layer = self._memory(geom, title, spec)
            if self.folder:
                self._load_into(layer, key)
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

    def _tables(self):
        """Names of the tables in the operation's GeoPackage (no errors if it is new)."""
        path = self.gpkg
        if not path or not os.path.exists(path):
            return []
        from osgeo import ogr
        ds = ogr.Open(path)
        if ds is None:
            return []
        names = [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())]
        ds = None
        return names

    def _load_into(self, layer, table):
        """Copy the rows of a GeoPackage table into a memory layer (matching fields by name)."""
        if table not in self._tables():
            return 0
        src = QgsVectorLayer(f"{self.gpkg}|layername={table}", table, "ogr")
        if not src.isValid():
            return 0
        names = [n for n in layer.fields().names() if src.fields().indexOf(n) >= 0]
        feats = []
        for f in src.getFeatures():
            nf = QgsFeature(layer.fields())
            nf.setGeometry(f.geometry())
            for n in names:
                nf.setAttribute(layer.fields().indexOf(n), f[n])
            feats.append(nf)
        del src
        if feats:
            layer.dataProvider().addFeatures(feats)
            layer.updateExtents()
        return len(feats)

    def _read_meta(self):
        import json
        try:
            with open(self.meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return
        self.name = meta.get("name") or self.name
        self.user_specs = list(meta.get("user_layers") or [])
        self.data_layers = [list(x) for x in meta.get("data_layers") or []]

    # ---- persistence ----------------------------------------------------
    def _on_changed(self, key):
        if key in SCHEMA:
            self.touch()

    def touch(self):
        """Something worth saving changed: (re)start the autosave countdown."""
        if self.folder and not self.folder_closed:
            self._dirty = True
            self._timer.start()

    def register_user_layer(self, layer):
        if layer not in self.user_layers:
            self.user_layers.append(layer)
            for sig in (layer.dataChanged, layer.afterCommitChanges, layer.nameChanged):
                sig.connect(self.touch)
            self.touch()

    def forget_user_layer(self, layer):
        if layer in self.user_layers:
            self.user_layers.remove(layer)
            self.touch()

    @staticmethod
    def _slug(name):
        import re
        return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "layer"

    def save(self):
        """Write every COP and user layer to the GeoPackage (one small file, always consistent)."""
        self._timer.stop()
        if not self.folder:
            return False
        import json
        path = self.gpkg
        tmp = os.path.join(self.folder, "cop.saving.gpkg")
        if os.path.exists(tmp):
            os.remove(tmp)
        ctx = QgsProject.instance().transformContext()
        first = True
        specs = []
        todo = [(key, self.layers[key]) for key in SCHEMA]
        used = set(SCHEMA)
        for layer in list(self.user_layers):
            try:
                layer.name()
            except RuntimeError:               # layer was removed from the project
                self.user_layers.remove(layer)
                continue
            table = "user_" + self._slug(layer.name())
            while table in used:
                table += "_2"
            used.add(table)
            specs.append({"table": table, "name": layer.name(),
                          "color": layer.customProperty("badr/color") or "#ffd24a"})
            todo.append((table, layer))
        for table, layer in todo:
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = table
            opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                         else QgsVectorFileWriter.CreateOrOverwriteLayer)
            res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, tmp, ctx, opts)
            if res[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"could not save {table}: {res[1]}")
            first = False
        os.replace(tmp, path)                  # atomic: a crash never leaves a half-written operation
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump({"name": self.name, "user_layers": specs, "data_layers": self.data_layers}, fh, indent=1)
        self._dirty = False
        self.saved.emit()
        return True

    def _autosave(self):
        """Timer slot: must never raise (an exception in a Qt slot aborts the app)."""
        try:
            self.save()
        except Exception as exc:
            self._dirty = False
            print(f"[BADR] autosave skipped: {exc}")

    def close(self):
        """Flush pending edits and stop saving (the layers are about to go away)."""
        self._timer.stop()
        if self._dirty:
            try:
                self.save()
            except Exception as exc:
                print(f"[BADR] could not save the operation: {exc}")
        self._dirty = False
        self.folder_closed = True

    folder_closed = False

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
                       if layer.fields().indexOf(k) >= 0 and f[k] != v}
            if changes:
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
