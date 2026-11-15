"""Catalog: create and manage geodatabases, feature datasets, feature classes, tables and domains.

The tree mirrors the ArcGIS Catalog pane.  "Contents" (the layer list) is what
is drawn on the map; "Catalog" is what exists on disk and can be added to it.
"""
import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from ..core import geodatabase as G
from ..core.settings import Settings
from . import theme
from .panels import Panel, button, h2, hint, row

ROLE = Qt.ItemDataRole.UserRole
GEOM_LABEL = {"point": "points", "line": "lines", "polygon": "polygons", "table": "table", "other": "geometry"}
GLYPH = {"point": "•", "line": "╱", "polygon": "▱", "table": "▤", "other": "?"}
GIS_FILES = (".shp", ".geojson", ".json", ".kml", ".kmz", ".gpx", ".csv", ".tif", ".tiff", ".jp2", ".vrt", ".mbtiles")


def crs_choices(wb):
    """[(label, epsg)] offered when creating data."""
    out = [("WGS 84 - latitude / longitude (EPSG:4326)", 4326)]
    c = wb.center_wgs84()
    if c:
        from ..analysis.raster import utm_epsg
        e = utm_epsg(c[0], c[1])
        out.append((f"UTM zone of the current view - metres (EPSG:{e})", e))
    out.append(("Web Mercator (EPSG:3857)", 3857))
    return out


class FieldsTable(QTableWidget):
    """Editable list of fields: name, type, alias, domain."""

    def __init__(self, domains=()):
        super().__init__(0, 4)
        self.domains = list(domains)
        self.setHorizontalHeaderLabels(["Field name", "Type", "Alias", "Domain"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setMinimumHeight(150)

    def add_row(self, name="", ftype="Text", alias="", domain=""):
        r = self.rowCount()
        self.insertRow(r)
        self.setItem(r, 0, QTableWidgetItem(name))
        t = QComboBox()
        t.addItems(list(G.FIELD_TYPES))
        t.setCurrentText(ftype)
        self.setCellWidget(r, 1, t)
        self.setItem(r, 2, QTableWidgetItem(alias))
        d = QComboBox()
        d.addItem("")
        d.addItems(self.domains)
        d.setCurrentText(domain)
        self.setCellWidget(r, 3, d)

    def remove_current(self):
        if self.currentRow() >= 0:
            self.removeRow(self.currentRow())

    def specs(self):
        out = []
        for r in range(self.rowCount()):
            name = (self.item(r, 0).text() if self.item(r, 0) else "").strip()
            if not name:
                continue
            out.append({"name": name, "type": self.cellWidget(r, 1).currentText(),
                        "alias": (self.item(r, 2).text() if self.item(r, 2) else "").strip(),
                        "domain": self.cellWidget(r, 3).currentText()})
        return out


class FeatureClassDialog(QDialog):
    def __init__(self, wb, gdb, dataset=None, table=False, parent=None):
        super().__init__(parent)
        self.gdb, self.table = gdb, table
        self.setWindowTitle("New table" if table else "New feature class")
        self.setMinimumWidth(520)
        info = G.contents(gdb)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("letters, digits and underscore, e.g. water_points")
        form.addRow("Name", self.name)
        self.geometry = QComboBox()
        for label, key in (("Points", "point"), ("Lines", "line"), ("Polygons", "polygon")):
            self.geometry.addItem(label, key)
        self.dataset = QComboBox()
        self.dataset.addItem("(none - directly in the geodatabase)", "")
        for d in sorted(info["datasets"]):
            self.dataset.addItem(d, d)
        if dataset:
            self.dataset.setCurrentIndex(max(0, self.dataset.findData(dataset)))
        self.crs = QComboBox()
        for label, epsg in crs_choices(wb):
            self.crs.addItem(label, epsg)
        self.crs.setEditable(True)
        self.crs.setToolTip("Pick one, or type any EPSG code")
        if not table:
            form.addRow("Geometry", self.geometry)
            if G.kind(gdb) == "gdb":
                form.addRow("Feature dataset", self.dataset)
            form.addRow("Coordinate system", self.crs)
            self.dataset.currentIndexChanged.connect(self._dataset_changed)
        lay.addLayout(form)
        lay.addWidget(h2("Fields"))
        self.fields = FieldsTable(sorted(info["domains"]))
        self.fields.add_row("name", "Text", "Name")
        self.fields.add_row("notes", "Text", "Notes")
        lay.addWidget(self.fields)
        lay.addWidget(row(button("Add field", lambda: self.fields.add_row()),
                          button("Remove field", self.fields.remove_current)))
        self.note = hint("")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._dataset_changed()

    def _dataset_changed(self):
        d = self.dataset.currentData()
        shared = G.dataset_epsg(self.gdb, d) if d else None
        self.crs.setEnabled(not shared)
        self.note.setText(f"Feature classes in '{d}' share its coordinate system (EPSG:{shared})." if shared else "")

    def epsg(self):
        data = self.crs.currentData()
        text = self.crs.currentText().strip()
        if data and self.crs.itemText(self.crs.currentIndex()) == text:
            return int(data)
        digits = "".join(ch for ch in text.split(":")[-1] if ch.isdigit())
        if not digits:
            raise G.GdbError("type an EPSG code, e.g. 32642")
        return int(digits)

    def create(self):
        return G.create_feature_class(self.gdb, self.name.text(), "table" if self.table else self.geometry.currentData(),
                                      4326 if self.table else self.epsg(),
                                      None if self.table else (self.dataset.currentData() or None),
                                      self.fields.specs())


class DomainDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New coded-value domain")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. RoadSurface")
        self.desc = QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Description", self.desc)
        lay.addLayout(form)
        self.table = QTableWidget(3, 2)
        self.table.setHorizontalHeaderLabels(["Code (stored)", "Description (shown)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        lay.addWidget(row(button("Add value", lambda: self.table.insertRow(self.table.rowCount()))))
        lay.addWidget(hint("A domain limits a field to a fixed list of values. Attach it to a field when you "
                           "create a feature class or add a field."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        out = {}
        for r in range(self.table.rowCount()):
            code = self.table.item(r, 0).text().strip() if self.table.item(r, 0) else ""
            text = self.table.item(r, 1).text().strip() if self.table.item(r, 1) else ""
            if code:
                out[code] = text or code
        return out


class PropertiesDialog(QDialog):
    """Design view of a feature class: summary plus field management."""

    def __init__(self, gdb, layer, parent=None):
        super().__init__(parent)
        self.gdb, self.layer = gdb, layer
        self.setWindowTitle(f"Properties - {layer}")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        lay.addWidget(h2("Fields"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field name", "Type", "Alias", "Domain"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        lay.addWidget(row(button("Add field", self.add_field), button("Delete field", self.delete_field)))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.refresh()

    def refresh(self):
        info = G.layer_info(self.gdb, self.layer)
        where = f"feature dataset {info['dataset']}" if info.get("dataset") else os.path.basename(self.gdb)
        self.summary.setText(
            f"<b>{self.layer}</b> &nbsp; {GEOM_LABEL.get(info['geometry'], '')} &nbsp;&middot;&nbsp; "
            f"{info['count']} rows &nbsp;&middot;&nbsp; in {where}<br>"
            f"<span style='color:{theme.MUTED}'>{info['crs'] or 'no geometry'}"
            f"{' (EPSG:%s)' % info['epsg'] if info['epsg'] else ''}</span>")
        self.table.setRowCount(len(info["fields"]))
        for r, f in enumerate(info["fields"]):
            for c, key in enumerate(("name", "type", "alias", "domain")):
                self.table.setItem(r, c, QTableWidgetItem(str(f[key])))

    def add_field(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add field")
        lay = QVBoxLayout(dlg)
        ft = FieldsTable(sorted(G.contents(self.gdb)["domains"]))
        ft.add_row()
        ft.setMinimumHeight(70)
        lay.addWidget(ft)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() and ft.specs():
            try:
                G.add_field(self.gdb, self.layer, ft.specs()[0])
            except G.GdbError as exc:
                QMessageBox.warning(self, "Add field", str(exc))
            self.refresh()

    def delete_field(self):
        r = self.table.currentRow()
        if r < 0:
            return
        name = self.table.item(r, 0).text()
        if QMessageBox.question(self, "Delete field", f"Delete field '{name}' and its values?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            G.delete_field(self.gdb, self.layer, name)
        except G.GdbError as exc:
            QMessageBox.warning(self, "Delete field", str(exc))
        self.refresh()


class CatalogPanel(Panel):
    title = "Catalog"
    stretch_at_end = False

    def build(self):
        self.lay.addWidget(hint("Geodatabases on disk. Create feature datasets, feature classes, tables and "
                                "domains here, then add them to the map. File geodatabases (.gdb) open directly "
                                "in ArcGIS."))
        self.lay.addWidget(row(button("New geodatabase", self.new_database, primary=True),
                               button("Connect", self.connect_existing, tip="Add an existing .gdb, .gpkg or folder")))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.add_to_map(it))
        self.tree.currentItemChanged.connect(lambda *_: self._enable())
        self.lay.addWidget(self.tree, 1)
        self.b_dataset = button("Feature dataset", self.new_dataset, tip="A group of feature classes sharing one "
                                                                         "coordinate system")
        self.b_class = button("Feature class", self.new_class)
        self.b_table = button("Table", lambda: self.new_class(table=True))
        self.b_domain = button("Domain", self.new_domain, tip="A list of allowed values for a field")
        self.lay.addWidget(h2("New"))
        self.lay.addWidget(row(self.b_dataset, self.b_class))
        self.lay.addWidget(row(self.b_table, self.b_domain))
        self.b_add = button("Add to map", lambda: self.add_to_map(self.tree.currentItem()), primary=True)
        self.b_import = button("Import layer", self.import_layer, tip="Copy a map layer into this geodatabase")
        self.b_props = button("Properties", self.properties, tip="Fields, coordinate system, row count")
        self.lay.addWidget(h2("Selected"))
        self.lay.addWidget(row(self.b_add, self.b_import))
        self.lay.addWidget(row(self.b_props, button("Refresh", self.refresh)))
        self.refresh()

    def on_operation(self):
        self.refresh()

    # ---- connections -----------------------------------------------------
    @staticmethod
    def _saved():
        try:
            return [p for p in json.loads(Settings.get("catalog/connections", "[]") or "[]") if os.path.exists(p)]
        except ValueError:
            return []

    def _connect(self, path):
        conns = self._saved()
        if path not in conns:
            conns.append(path)
            Settings.set("catalog/connections", json.dumps(conns))

    def _disconnect(self, path):
        Settings.set("catalog/connections", json.dumps([p for p in self._saved() if p != path]))
        self.refresh()

    def data_folder(self):
        op = self.win.wb.operation
        base = op.folder if op and op.folder else os.path.join(os.path.expanduser("~"), "BADR")
        return os.path.join(base, "data")

    def databases(self):
        found = []
        folder = self.data_folder()
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if G.kind(name):
                    found.append(os.path.join(folder, name))
        for p in self._saved():
            if G.kind(p) and p not in found:
                found.append(p)
        return found

    # ---- tree ------------------------------------------------------------
    def refresh(self):
        expanded = set()
        current = self._key(self.tree.currentItem())

        def remember(it):
            if it.isExpanded():
                expanded.add(self._key(it))
            for i in range(it.childCount()):
                remember(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            remember(self.tree.topLevelItem(i))
        first_time = self.tree.topLevelItemCount() == 0
        self.tree.clear()
        for path in self.databases():
            self._add_database(path)
        for path in self._saved():
            if os.path.isdir(path) and not G.kind(path):
                self._add_folder(path)
        if self.tree.topLevelItemCount() == 0:
            it = QTreeWidgetItem(["No geodatabase yet - create one above"])
            it.setForeground(0, QColor(theme.MUTED))
            it.setData(0, ROLE, {"type": "empty"})
            self.tree.addTopLevelItem(it)

        def restore(it):
            key = self._key(it)
            if first_time or key in expanded:
                it.setExpanded(True)
            if key == current:
                self.tree.setCurrentItem(it)
            for i in range(it.childCount()):
                restore(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            restore(self.tree.topLevelItem(i))
        self._enable()

    @staticmethod
    def _key(item):
        if item is None:
            return None
        d = item.data(0, ROLE) or {}
        return (d.get("type"), d.get("gdb") or d.get("path"), d.get("name"))

    def _add_database(self, path):
        try:
            info = G.contents(path)
        except G.GdbError as exc:
            it = QTreeWidgetItem([f"{os.path.basename(path)}  (unreadable)"])
            it.setToolTip(0, str(exc))
            it.setData(0, ROLE, {"type": "broken", "gdb": path})
            self.tree.addTopLevelItem(it)
            return
        top = QTreeWidgetItem([os.path.basename(path)])
        top.setToolTip(0, path)
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        top.setData(0, ROLE, {"type": "gdb", "gdb": path})
        for dname in sorted(info["datasets"]):
            rows = info["datasets"][dname]
            d = QTreeWidgetItem([f"{dname}  ·  feature dataset" + ("" if rows else " (empty)")])
            d.setData(0, ROLE, {"type": "dataset", "gdb": path, "name": dname})
            for r in rows:
                d.addChild(self._layer_item(path, r))
            top.addChild(d)
        for r in info["layers"]:
            top.addChild(self._layer_item(path, r))
        if info["domains"]:
            doms = QTreeWidgetItem(["Domains"])
            doms.setForeground(0, QColor(theme.MUTED))
            doms.setData(0, ROLE, {"type": "domains", "gdb": path})
            for name, values in sorted(info["domains"].items()):
                it = QTreeWidgetItem([f"{name}  ({len(values)} values)"])
                it.setToolTip(0, "\n".join(f"{k} = {v}" for k, v in values.items()))
                it.setData(0, ROLE, {"type": "domain", "gdb": path, "name": name})
                doms.addChild(it)
            top.addChild(doms)
        self.tree.addTopLevelItem(top)

    @staticmethod
    def _layer_item(path, r):
        it = QTreeWidgetItem([f"{GLYPH.get(r['geometry'], '?')}  {r['name']}   ({r['count']})"])
        it.setToolTip(0, f"{GEOM_LABEL.get(r['geometry'], '')}, {r['count']} rows"
                         + (f", EPSG:{r['epsg']}" if r["epsg"] else ""))
        it.setData(0, ROLE, {"type": "table" if r["geometry"] == "table" else "class", "gdb": path,
                             "name": r["name"], "dataset": r.get("dataset")})
        return it

    def _add_folder(self, path):
        top = QTreeWidgetItem([os.path.basename(path.rstrip("/\\")) or path])
        top.setToolTip(0, path)
        top.setData(0, ROLE, {"type": "folder", "path": path})
        try:
            names = sorted(os.listdir(path))
        except OSError:
            names = []
        for name in names[:300]:
            full = os.path.join(path, name)
            if G.kind(name):
                it = QTreeWidgetItem([name])
                it.setData(0, ROLE, {"type": "dbfile", "path": full})
                top.addChild(it)
            elif name.lower().endswith(GIS_FILES):
                it = QTreeWidgetItem([name])
                it.setData(0, ROLE, {"type": "file", "path": full})
                top.addChild(it)
        self.tree.addTopLevelItem(top)

    def _sel(self):
        it = self.tree.currentItem()
        return (it.data(0, ROLE) or {}) if it else {}

    def _gdb(self):
        """Geodatabase the current selection belongs to (the only one, if nothing is selected)."""
        d = self._sel()
        if d.get("gdb"):
            return d["gdb"]
        dbs = self.databases()
        return dbs[0] if len(dbs) == 1 else None

    def _enable(self):
        d = self._sel()
        gdb = self._gdb()
        is_fgdb = bool(gdb) and G.kind(gdb) == "gdb"
        self.b_dataset.setEnabled(is_fgdb)
        self.b_domain.setEnabled(is_fgdb)
        self.b_class.setEnabled(bool(gdb))
        self.b_table.setEnabled(bool(gdb))
        self.b_import.setEnabled(bool(gdb))
        self.b_add.setEnabled(d.get("type") in ("class", "table", "file", "dataset", "gdb", "dbfile"))
        self.b_props.setEnabled(d.get("type") in ("class", "table"))

    def _fail(self, exc):
        self.win.say(str(exc), "error")

    # ---- actions ---------------------------------------------------------
    def new_database(self):
        folder = self.data_folder()
        os.makedirs(folder, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "New geodatabase", os.path.join(folder, "operation.gdb"),
                                              "File geodatabase (*.gdb);;GeoPackage (*.gpkg)")
        if not path:
            return
        self.create_database(path)

    def create_database(self, path):
        try:
            path = G.create(path)
        except G.GdbError as exc:
            return self._fail(exc)
        self._connect(path)
        self.refresh()
        self._select(("gdb", path, None))
        self.win.say(f"Geodatabase created: {path}")
        return path

    def connect_existing(self):
        m = QMenu(self)
        m.addAction("File geodatabase (.gdb folder)...", lambda: self._pick_dir(True))
        m.addAction("GeoPackage (.gpkg)...", self._pick_gpkg)
        m.addAction("Folder of GIS files...", lambda: self._pick_dir(False))
        m.exec(self.cursor().pos())

    def _pick_dir(self, gdb):
        d = QFileDialog.getExistingDirectory(self, "Select the .gdb folder" if gdb else "Select a folder")
        if not d:
            return
        if gdb and G.kind(d) != "gdb":
            return self.win.say("That folder is not a file geodatabase (its name must end in .gdb).", "warn")
        self._connect(d)
        self.refresh()

    def _pick_gpkg(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select a GeoPackage", "", "GeoPackage (*.gpkg)")
        if p:
            self._connect(p)
            self.refresh()

    def _select(self, key):
        def walk(it):
            if self._key(it) == key:
                self.tree.setCurrentItem(it)
                it.setExpanded(True)
                return True
            return any(walk(it.child(i)) for i in range(it.childCount()))
        for i in range(self.tree.topLevelItemCount()):
            if walk(self.tree.topLevelItem(i)):
                return

    def new_dataset(self):
        gdb = self._gdb()
        if not gdb:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("New feature dataset")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        name = QLineEdit()
        name.setPlaceholderText("e.g. Transport, Hydrology, Boundaries")
        crs = QComboBox()
        for label, epsg in crs_choices(self.win.wb):
            crs.addItem(label, epsg)
        form.addRow("Name", name)
        form.addRow("Coordinate system", crs)
        lay.addLayout(form)
        lay.addWidget(hint("Every feature class placed in this dataset will use this coordinate system."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec():
            return
        try:
            made = G.create_dataset(gdb, name.text(), crs.currentData())
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()
        self._select(("dataset", gdb, made))

    def new_class(self, table=False):
        gdb = self._gdb()
        if not gdb:
            return
        d = self._sel()
        dataset = d.get("name") if d.get("type") == "dataset" else d.get("dataset")
        dlg = FeatureClassDialog(self.win.wb, gdb, dataset, table, self)
        if not dlg.exec():
            return
        try:
            made = dlg.create()
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()
        self._select(("table" if table else "class", gdb, made))
        self.win.say(f"{'Table' if table else 'Feature class'} '{made}' created. Add it to the map to draw into it.")

    def new_domain(self):
        gdb = self._gdb()
        if not gdb:
            return
        dlg = DomainDialog(self)
        if not dlg.exec():
            return
        try:
            G.create_domain(gdb, dlg.name.text(), dlg.values(), dlg.desc.text())
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()

    def add_to_map(self, item):
        d = (item.data(0, ROLE) or {}) if item else {}
        t = d.get("type")
        try:
            if t in ("class", "table"):
                self.win.add_catalog_layer(d["gdb"], d["name"])
            elif t == "dataset":
                for i in range(item.childCount()):
                    c = item.child(i).data(0, ROLE)
                    self.win.add_catalog_layer(c["gdb"], c["name"])
            elif t == "file":
                self.win.add_path(d["path"])
            elif t == "dbfile":
                self._connect(d["path"])
                self.refresh()
        except G.GdbError as exc:
            self._fail(exc)

    def import_layer(self):
        gdb = self._gdb()
        if not gdb:
            return
        from qgis.core import QgsMapLayer
        layers = [l for l in self.win.wb.project.mapLayers().values()
                  if l.type() == QgsMapLayer.VectorLayer and l.featureCount() > 0]
        if not layers:
            return self.win.say("There is no map layer with features to import.", "warn")
        names = [l.name() for l in layers]
        name, ok = QInputDialog.getItem(self, "Import layer", "Copy this map layer into the geodatabase:", names, 0,
                                        False)
        if not ok:
            return
        d = self._sel()
        dataset = d.get("name") if d.get("type") == "dataset" else None
        try:
            made = G.import_layer(layers[names.index(name)], gdb, None, dataset)
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()
        self._select(("class", gdb, made))
        self.win.say(f"Imported '{name}' as feature class '{made}'.")

    def properties(self):
        d = self._sel()
        if d.get("type") in ("class", "table"):
            PropertiesDialog(d["gdb"], d["name"], self).exec()
            self.refresh()

    def rename(self):
        d = self._sel()
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=d.get("name", ""))
        if not ok or not new.strip():
            return
        try:
            self.win.release_catalog_layer(d["gdb"], d["name"])
            G.rename_layer(d["gdb"], d["name"], new)
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()

    def delete(self):
        d = self._sel()
        t = d.get("type")
        what = {"class": "feature class", "table": "table", "dataset": "feature dataset and everything in it"}.get(t)
        if not what:
            return
        if QMessageBox.question(self, "Delete", f"Delete {what} '{d['name']}'? This cannot be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            if t == "dataset":
                for r in G.contents(d["gdb"])["datasets"].get(d["name"], []):
                    self.win.release_catalog_layer(d["gdb"], r["name"])
                G.delete_dataset(d["gdb"], d["name"])
            else:
                self.win.release_catalog_layer(d["gdb"], d["name"])
                G.delete_layer(d["gdb"], d["name"])
        except G.GdbError as exc:
            return self._fail(exc)
        self.refresh()

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        d = item.data(0, ROLE) or {}
        t = d.get("type")
        m = QMenu(self)
        if t == "gdb":
            if G.kind(d["gdb"]) == "gdb":
                m.addAction("New feature dataset...", self.new_dataset)
            m.addAction("New feature class...", self.new_class)
            m.addAction("New table...", lambda: self.new_class(table=True))
            if G.kind(d["gdb"]) == "gdb":
                m.addAction("New domain...", self.new_domain)
            m.addAction("Import map layer...", self.import_layer)
            m.addSeparator()
            m.addAction("Disconnect (keeps the files)", lambda: self._disconnect(d["gdb"]))
        elif t == "dataset":
            m.addAction("New feature class here...", self.new_class)
            m.addAction("Import map layer here...", self.import_layer)
            m.addAction("Add all to map", lambda: self.add_to_map(item))
            m.addSeparator()
            m.addAction("Delete...", self.delete)
        elif t in ("class", "table"):
            m.addAction("Add to map", lambda: self.add_to_map(item))
            m.addAction("Properties and fields...", self.properties)
            m.addAction("Rename...", self.rename)
            m.addSeparator()
            m.addAction("Delete...", self.delete)
        elif t == "file":
            m.addAction("Add to map", lambda: self.add_to_map(item))
        elif t == "folder":
            m.addAction("Disconnect", lambda: self._disconnect(d["path"]))
        elif t == "dbfile":
            m.addAction("Connect", lambda: self.add_to_map(item))
        if m.actions():
            m.exec(self.tree.viewport().mapToGlobal(pos))
