"""Workflow panels.  Seven modes replace QGIS' menus, toolbars and docks.

Every button calls the same Toolbox function the AI uses (``win.do``), so the
manual and assisted paths can never drift apart.
"""
from datetime import date, timedelta

from qgis.core import QgsLayerTreeModel
from qgis.gui import QgsLayerTreeView
from qgis.PyQt.QtCore import QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..analysis import pattern
from ..analysis.capabilities import CapabilityIndex
from ..core.workbench import BASEMAPS
from ..sensors.decoders import DECODERS
from ..sensors.manager import PRESETS
from ..sensors.model import KINDS
from ..sensors.transports import TRANSPORTS
from ..symbology import sidc as S
from ..symbology.engine import SymbolEngine
from ..symbology.palette import COLORS, GRAPHIC_KINDS, PALETTE, item_sidc
from . import theme


def h1(text):
    lab = QLabel(text)
    lab.setObjectName("h1")
    return lab


def h2(text):
    lab = QLabel(text)
    lab.setObjectName("h2")
    return lab


def hint(text):
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


def button(text, slot=None, primary=False, tip=""):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


def row(*widgets):
    w = QWidget()
    w.setObjectName("bare")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for x in widgets:
        lay.addWidget(x, 1 if isinstance(x, QLineEdit) else 0)
    return w


class Panel(QWidget):
    title = ""

    def __init__(self, win):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("bare")
        body = QWidget()
        body.setObjectName("bare")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(12, 10, 12, 12)
        self.lay.setSpacing(6)
        self.lay.addWidget(h1(self.title))
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.build()
        if self.stretch_at_end:
            self.lay.addStretch(1)

    stretch_at_end = True

    def build(self):
        pass

    def on_operation(self):
        pass


# ---------------------------------------------------------------------------
class PicturePanel(Panel):
    """Contents: what is drawn on the map, in drawing order."""

    title = "Contents"
    stretch_at_end = False

    def build(self):
        w = self.win
        self.lay.addWidget(row(button("New", w.new_operation), button("Open", w.open_operation_dialog),
                               button("Export data", w.export_data, primary=True,
                                      tip="GeoPackage, Shapefile, GeoJSON, KML, CSV, file geodatabase")))
        self.lay.addWidget(h2("Layers"))
        self.tree = QgsLayerTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(f"QTreeView {{ background: {theme.BG}; color: {theme.TEXT}; }}")
        self.tree.setIndentation(14)
        self.lay.addWidget(self.tree, 1)
        self.tree.doubleClicked.connect(lambda _i: w.open_table(self.tree.currentLayer()))
        from .main_window import LayerMenu
        self._menu = LayerMenu(w, self.tree)
        self.tree.setMenuProvider(self._menu)
        self.lay.addWidget(row(button("Add file", w.add_file_layer, tip="Any GIS file QGIS can read"),
                               button("Table", lambda: w.open_table(self.tree.currentLayer()),
                                      tip="Attribute table of the selected layer (or double-click the layer)"),
                               button("Zoom", self.zoom_selected), button("Remove", self.remove_selected)))
        self.lay.addWidget(hint("Double-click a layer for its attribute table. Right-click for more."))
        self.lay.addWidget(h2("Basemap"))
        self.basemap = QComboBox()
        self.basemap.addItems(["None (offline)"] + list(BASEMAPS))
        self.basemap.currentTextChanged.connect(lambda name: w.wb.set_basemap(name))
        self.lay.addWidget(self.basemap)
        self.counts = hint("")
        self.lay.addWidget(self.counts)
        self.lay.addWidget(row(button("Save map image", w.export_image)))
        self.on_operation()

    def on_operation(self):
        wb = self.win.wb
        self.model = QgsLayerTreeModel(wb.project.layerTreeRoot())
        self.model.setFlag(QgsLayerTreeModel.AllowNodeChangeVisibility)
        self.model.setFlag(QgsLayerTreeModel.AllowNodeReorder)
        self.tree.setModel(self.model)
        self.tree.expandAll()
        if wb.operation:
            wb.operation.changed.connect(lambda _k: self.refresh_counts())
        self.refresh_counts()

    def refresh_counts(self):
        op = self.win.wb.operation
        if not op:
            return
        n = {k: op.layer(k).featureCount() for k in ("units", "incidents", "lines", "areas", "tracks")}
        self.counts.setText(f"{n['units']} units/sites   {n['incidents']} incidents\n"
                            f"{n['lines'] + n['areas']} control measures   {n['tracks']} live tracks")

    def remove_selected(self):
        wb = self.win.wb
        cop = set(wb.operation.layers.values())
        for layer in self.tree.selectedLayers():
            if layer in cop:
                self.win.say("COP layers cannot be removed; hide them with the checkbox instead.", "warn")
            else:
                wb.project.removeMapLayer(layer.id())

    def zoom_selected(self):
        for layer in self.tree.selectedLayers():
            self.win.wb.zoom_to_layer(layer)
            break


# ---------------------------------------------------------------------------
def _graphic_icon(kind, size=34):
    abbr, color_key, style = GRAPHIC_KINDS[kind]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(COLORS[color_key]))
    pen.setWidth(2)
    if "dash" in style:
        pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    is_area = any(i.get("kind") == kind for i in PALETTE["Control areas"])
    if is_area:
        p.drawRoundedRect(5, 8, size - 10, size - 16, 6, 6)
    else:
        p.drawLine(4, size - 9, size - 5, 9)
        if style.startswith("arrow"):
            p.drawLine(size - 5, 9, size - 13, 10)
            p.drawLine(size - 5, 9, size - 7, 17)
    p.end()
    return pm


class PlanPanel(Panel):
    title = "Plan"
    stretch_at_end = False

    def build(self):
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all NATO APP-6D symbols...")
        self.search.textChanged.connect(self.refresh)
        self.lay.addWidget(self.search)

        self.aff = {}
        aff_row = []
        for key, label in (("friend", "Friend"), ("hostile", "Hostile"), ("neutral", "Neutral"), ("unknown", "Unk")):
            b = QPushButton(label)
            b.setStyleSheet("padding:5px 4px;")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setToolTip("Affiliation override for the next symbol placed")
            b.clicked.connect(self.refresh)
            self.aff[key] = b
            aff_row.append(b)
        self.default_aff = QPushButton("Auto")
        self.default_aff.setCheckable(True)
        self.default_aff.setAutoExclusive(True)
        self.default_aff.setChecked(True)
        self.default_aff.setStyleSheet("padding:5px 4px;")
        self.default_aff.clicked.connect(self.refresh)
        self.lay.addWidget(row(self.default_aff, *aff_row))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. A/1-5, OBJ FOX, PL RED")
        self.echelon = QComboBox()
        self.echelon.addItem("(as palette)", "")
        for e in ("team", "squad", "section", "platoon", "company", "battalion", "regiment", "brigade", "division"):
            self.echelon.addItem(e.capitalize(), e)
        self.status = QComboBox()
        self.status.addItem("Present", "present")
        self.status.addItem("Planned / suspected", "planned")
        form.addRow("Designation", self.name)
        form.addRow("Echelon", self.echelon)
        form.addRow("Status", self.status)
        self.lay.addLayout(form)

        self.group = QComboBox()
        self.group.addItems(list(PALETTE))
        self.group.setCurrentText("Threat - irregular")
        self.group.currentTextChanged.connect(self.refresh)
        self.lay.addWidget(self.group)

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(44, 44))
        self.list.setGridSize(QSize(92, 80))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemClicked.connect(self.activate)
        self.lay.addWidget(self.list, 1)
        self.lay.addWidget(hint("Pick a symbol, then click the map. Lines and areas: click vertices, "
                                "right-click to finish. Or just tell BADR what to draw."))
        self.refresh()

    def _affiliation(self):
        for key, b in self.aff.items():
            if b.isChecked():
                return key
        return None

    def refresh(self, *_):
        eng = SymbolEngine.instance()
        self.list.clear()
        q = self.search.text().strip()
        aff = self._affiliation()
        self.group.setVisible(not q)
        if q:
            for hit in S.search(q, limit=24):
                if hit["symbol_set"] == "25" and hit.get("geom", "point") not in ("point", ""):
                    continue
                code = S.build(hit["symbol_set"], hit["entity"], aff or "friend")
                it = QListWidgetItem(QIcon(eng.pixmap(code, 44)), hit["name"].split(" / ")[-1][:26])
                it.setToolTip(f"{hit['set_name']}: {hit['name']}\nSIDC {code}")
                it.setData(Qt.ItemDataRole.UserRole, {"sidc": code,
                                                      "layer": "incidents" if hit["symbol_set"] == "40" else "units",
                                                      "label": hit["name"].split(" / ")[-1]})
                self.list.addItem(it)
            return
        for item in PALETTE[self.group.currentText()]:
            if item.get("layer") in ("lines", "areas"):
                it = QListWidgetItem(QIcon(_graphic_icon(item["kind"], 44)), item["label"])
                it.setData(Qt.ItemDataRole.UserRole, dict(item))
            else:
                code = item_sidc(item, aff)
                it = QListWidgetItem(QIcon(eng.pixmap(code, 44)), item["label"])
                it.setToolTip(f"{S.describe(code)}\nSIDC {code}")
                it.setData(Qt.ItemDataRole.UserRole, {"sidc": code, "layer": item.get("layer", "units"),
                                                      "label": item["label"]})
            self.list.addItem(it)

    def activate(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        win = self.win
        name = self.name.text().strip()
        if d.get("layer") in ("lines", "areas"):
            kind = d["kind"]

            def done(pts):
                win.do("draw_graphic", kind=kind, points=[list(p) for p in pts], name=name,
                       affiliation=self._affiliation() or "", status=self.status.currentData())
            win.sketch(d["layer"] == "areas", done, f"Draw {d['label']}: click vertices, right-click to finish",
                       COLORS[GRAPHIC_KINDS[kind][1]])
            return
        code = d["sidc"]
        if self.echelon.currentData():
            code = S.with_echelon(code, self.echelon.currentData())
        if self.status.currentData() == "planned":
            code = S.with_status(code, "planned")

        def place(pts):
            lat, lon = pts[0]
            if d["layer"] == "incidents":
                win.wb.operation.add_point("incidents", lat, lon, {"sidc": code, "kind": d["label"],
                                                                    "name": name or d["label"]})
                win.say(f"Incident logged: {d['label']}.")
            else:
                win.wb.operation.add_point("units", lat, lon, {"sidc": code, "name": name})
            win.wb.refresh()
            win.pick(1, place, f"Place {d['label']}: click the map (Esc to stop)")   # stay armed
        win.pick(1, place, f"Place {d['label']}: click the map (Esc to stop)")


# ---------------------------------------------------------------------------
class TerrainPanel(Panel):
    title = "Terrain"

    def build(self):
        w = self.win
        self.dem_state = hint("")
        self.lay.addWidget(self.dem_state)
        self.lay.addWidget(button("Get elevation for this view", lambda: w.do("get_elevation"), primary=True,
                                  tip="Earth Engine (Copernicus 30 m) when configured, otherwise open terrain tiles"))
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        self.mover = QComboBox()
        for k, label in (("dismounted", "Dismounted"), ("wheeled", "Wheeled"), ("tracked", "Tracked")):
            self.mover.addItem(label, k)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.2, 50)
        self.radius.setValue(5.0)
        self.radius.setSuffix(" km")
        self.height = QDoubleSpinBox()
        self.height.setRange(0.5, 500)
        self.height.setValue(2.0)
        self.height.setSuffix(" m")
        form.addRow("Mover", self.mover)
        form.addRow("Radius", self.radius)
        form.addRow("Observer height", self.height)
        self.lay.addLayout(form)

        self.lay.addWidget(h2("Visibility"))
        self.lay.addWidget(row(
            button("Viewshed", lambda: w.pick(1, lambda p: w.do(
                "terrain", analysis="viewshed", location=list(p[0]), radius_m=self.radius.value() * 1000,
                observer_height=self.height.value()), "Viewshed: click the observer position"),
                tip="What can be seen from a point"),
            button("Line of sight", lambda: w.pick(2, lambda p: w.do(
                "terrain", analysis="line_of_sight", location=list(p[0]), location2=list(p[1]),
                observer_height=self.height.value()), "Line of sight: click observer, then target"))))
        self.lay.addWidget(h2("Movement"))
        self.lay.addWidget(row(
            button("Mobility", lambda: w.do("terrain", analysis="mobility", mover=self.mover.currentData()),
                   tip="GO / SLOW-GO / NO-GO for the selected mover"),
            button("Slope", lambda: w.do("terrain", analysis="slope"))))
        self.lay.addWidget(row(
            button("Route", lambda: w.pick(2, lambda p: w.do(
                "terrain", analysis="route", location=list(p[0]), location2=list(p[1]),
                mover=self.mover.currentData()), "Route: click start, then destination"),
                tip="Least-cost cross-country route with time estimate"),
            button("Landing zones", lambda: w.do("terrain", analysis="landing_zones"),
                   tip="Flat patches large enough for a helicopter LZ")))
        self.lay.addWidget(h2("Measures"))
        self.rings = QLineEdit("1, 3, 5")
        self.rings.setToolTip("Ring radii in km")
        self.lay.addWidget(row(self.rings, button("Range rings", self._rings)))
        self.cell = QComboBox()
        for c in (100, 250, 500, 1000, 5000):
            self.cell.addItem(f"{c} m", c)
        self.cell.setCurrentIndex(2)
        self.lay.addWidget(row(self.cell, button("Reference grid", lambda: w.do(
            "terrain", analysis="grid", cell_m=self.cell.currentData()), tip="Gridded Reference Graphic for the view")))
        self.lay.addWidget(button("Cordon (inner / outer)", lambda: w.pick(1, lambda p: w.do(
            "terrain", analysis="cordon", location=list(p[0])), "Cordon: click the objective")))
        self.lay.addWidget(hint("Slope thresholds are planning approximations. Confirm with imagery and ground recce."))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1500)
        self._refresh()

    def _rings(self):
        try:
            radii = [float(x) * 1000 for x in self.rings.text().replace(";", ",").split(",") if x.strip()]
        except ValueError:
            self.win.say("Ring radii must be numbers in km, e.g. 1, 3, 5", "warn")
            return
        self.win.pick(1, lambda p: self.win.do("terrain", analysis="range_rings", location=list(p[0]), radii_m=radii),
                      "Range rings: click the centre")

    def _refresh(self):
        dem = self.win.wb.dem()
        if dem is None:
            self.dem_state.setText("No elevation model loaded.")
            self.dem_state.setStyleSheet(f"color:{theme.WARN};")
        else:
            self.dem_state.setText(f"Elevation: {dem.name()} ({dem.width()}x{dem.height()})")
            self.dem_state.setStyleSheet(f"color:{theme.OK};")


# ---------------------------------------------------------------------------
class HeatMatrix(QWidget):
    """Hour-of-day x day-of-week incident matrix."""

    def __init__(self):
        super().__init__()
        self.matrix = [[0] * 24 for _ in range(7)]
        self.setMinimumHeight(128)

    def set_matrix(self, m):
        self.matrix = m
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        left, top = 30, 14
        cw = max(4.0, (self.width() - left - 2) / 24.0)
        ch = max(8.0, (self.height() - top - 2) / 7.0)
        peak = max(1, max(max(r) for r in self.matrix))
        p.setPen(QColor(theme.MUTED))
        f = p.font()
        f.setPointSize(7)
        p.setFont(f)
        for h in range(0, 24, 3):
            p.drawText(int(left + h * cw), 10, f"{h:02d}")
        for d, name in enumerate(pattern.DAYS):
            p.drawText(2, int(top + d * ch + ch * 0.7), name)
            for h in range(24):
                v = self.matrix[d][h] / peak
                c = QColor(theme.PANEL_2) if v == 0 else QColor(255, int(190 - 150 * v), int(60 - 40 * v), int(90 + 165 * v))
                p.fillRect(int(left + h * cw) + 1, int(top + d * ch) + 1, int(cw) - 1, int(ch) - 1, c)
        p.end()


class IntelPanel(Panel):
    title = "Intel"

    def build(self):
        w = self.win
        self.lay.addWidget(h2("Log incident"))
        self.kind = QComboBox()
        for item in PALETTE["Incidents (SIGACTS)"]:
            self.kind.addItem(item["label"], item)
        self.lay.addWidget(self.kind)
        self.lay.addWidget(button("Place on map", self._log, primary=True))
        self.lay.addWidget(h2("Pattern analysis"))
        self.dist = QDoubleSpinBox()
        self.dist.setRange(50, 5000)
        self.dist.setValue(500)
        self.dist.setSuffix(" m")
        self.dist.setToolTip("Cluster distance / route corridor width")
        self.lay.addWidget(row(QLabel("Distance"), self.dist))
        self.lay.addWidget(row(
            button("Hotspots", lambda: w.do("incident_analysis", analysis="hotspots", distance_m=self.dist.value())),
            button("Route threat", lambda: w.do("incident_analysis", analysis="route_threat",
                                                distance_m=self.dist.value()))))
        self.lay.addWidget(h2("Pattern of life (Zulu)"))
        self.matrix = HeatMatrix()
        self.lay.addWidget(self.matrix)
        self.summary = hint("No incidents yet.")
        self.lay.addWidget(self.summary)
        self.lay.addWidget(h2("Ask"))
        for label, q in (("Where is the IED threat highest?",
                          "Where and when is the IED threat highest, and which of my routes is most exposed?"),
                         ("Short INTSUM", "Summarise the incident picture as a short INTSUM."),
                         ("Suggest NAIs", "Suggest NAIs to confirm or deny the pattern you see.")):
            b = QPushButton(label)
            b.setStyleSheet("text-align:left;")
            b.setToolTip(q)
            b.clicked.connect(lambda _=False, text=q: w.ask(text))
            self.lay.addWidget(b)
        self.on_operation()

    def on_operation(self):
        op = self.win.wb.operation
        if op:
            op.changed.connect(lambda key: key == "incidents" and self.refresh())
        self.refresh()

    def refresh(self):
        if not self.win.wb.operation:
            return
        t = pattern.temporal(self.win.wb)
        self.matrix.set_matrix(t["matrix"])
        self.summary.setText(t["summary"])

    def _log(self):
        item = self.kind.currentData()
        code = item_sidc(item)
        win = self.win

        def place(pts):
            lat, lon = pts[0]
            win.wb.operation.add_point("incidents", lat, lon, {"sidc": code, "kind": item["label"],
                                                                "name": item["label"]})
            win.wb.refresh()
            win.pick(1, place, f"Log {item['label']}: click the map (Esc to stop)")
        win.pick(1, place, f"Log {item['label']}: click the map (Esc to stop)")


# ---------------------------------------------------------------------------
class ImageryPanel(Panel):
    title = "Imagery"

    def build(self):
        from ..gee import client as gee
        from qgis.PyQt.QtWidgets import QDateEdit
        w = self.win
        self.state = hint("")
        self.lay.addWidget(self.state)
        self.lay.addWidget(h2("Earth Engine product"))
        self.product = QComboBox()
        for key, (label, what, _d) in gee.PRODUCTS.items():
            self.product.addItem(label, key)
            self.product.setItemData(self.product.count() - 1, what, Qt.ItemDataRole.ToolTipRole)
        self.lay.addWidget(self.product)
        today = date.today()

        def dedit(d):
            e = QDateEdit()
            e.setCalendarPopup(True)
            e.setDisplayFormat("yyyy-MM-dd")
            e.setDate(d)
            return e
        self.start, self.end = dedit(today - timedelta(days=60)), dedit(today)
        self.lay.addWidget(row(self.start, QLabel("to"), self.end))
        self.lay.addWidget(row(
            button("Add to map", lambda: w.do("imagery", action="add", product=self.product.currentData(),
                                              start=self._d(self.start), end=self._d(self.end)), primary=True),
            button("Download", lambda: w.do("imagery", action="download", product=self.product.currentData(),
                                            start=self._d(self.start), end=self._d(self.end)),
                   tip="Fetch pixel values for local analysis")))
        self.lay.addWidget(h2("Change detection"))
        self.b0, self.b1 = dedit(today - timedelta(days=120)), dedit(today - timedelta(days=90))
        self.a0, self.a1 = dedit(today - timedelta(days=30)), dedit(today)
        self.lay.addWidget(row(QLabel("Before"), self.b0, self.b1))
        self.lay.addWidget(row(QLabel("After "), self.a0, self.a1))
        self.method = QComboBox()
        self.method.addItem("Optical (Sentinel-2)", "optical")
        self.method.addItem("Radar, through cloud (Sentinel-1)", "sar")
        self.lay.addWidget(self.method)
        self.lay.addWidget(button("Detect change", lambda: w.do(
            "imagery", action="change", before_start=self._d(self.b0), before_end=self._d(self.b1),
            after_start=self._d(self.a0), after_end=self._d(self.a1), method=self.method.currentData())))
        self.lay.addWidget(h2("Reference data (OpenStreetMap)"))
        from ..analysis.osm import THEMES
        self.osm = QComboBox()
        self.osm.addItems(sorted(THEMES))
        self.osm.setCurrentText("roads")
        self.lay.addWidget(row(self.osm, button("Import for view", lambda: w.do(
            "reference_data", action="osm", name=self.osm.currentText()))))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(4000)
        self._refresh()

    @staticmethod
    def _d(edit):
        return edit.date().toString("yyyy-MM-dd")

    def _refresh(self):
        from ..gee import client as gee
        ready, msg = gee.status()
        self.state.setText(msg)
        self.state.setStyleSheet(f"color:{theme.OK if ready else theme.WARN};")


# ---------------------------------------------------------------------------
class SensorsPanel(Panel):
    title = "Sensors"
    stretch_at_end = False

    def build(self):
        w = self.win
        self.lay.addWidget(hint("Any transport with any protocol. Links are receive-only."))
        self.lay.addWidget(h2("New connection"))
        self.preset = QComboBox()
        self.preset.addItem("Custom...", None)
        for name in PRESETS:
            self.preset.addItem(name, name)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.lay.addWidget(self.preset)
        self.transport = QComboBox()
        for k, t in TRANSPORTS.items():
            self.transport.addItem(t.label, k)
        self.protocol = QComboBox()
        for k, d in DECODERS.items():
            self.protocol.addItem(d.label, k)
        self.transport.currentIndexChanged.connect(self._rebuild_form)
        self.protocol.currentIndexChanged.connect(self._rebuild_form)
        self.lay.addWidget(self.transport)
        self.lay.addWidget(self.protocol)
        self.form_host = QWidget()
        self.form_host.setObjectName("bare")
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.lay.addWidget(self.form_host)
        self.fields = {}
        self.lay.addWidget(row(button("Connect", self._connect, primary=True),
                               button("Simulate feed", w.toggle_simulator,
                                      tip="Built-in training feed: a UAS orbit and a patrol, sent over real UDP")))
        self.lay.addWidget(h2("Connections"))
        self.conns = QListWidget()
        self.conns.setMaximumHeight(96)
        self.lay.addWidget(self.conns)
        self.lay.addWidget(row(button("Start", lambda: self._conn_action("start")),
                               button("Stop", lambda: self._conn_action("stop")),
                               button("Remove", lambda: self._conn_action("remove"))))
        self.lay.addWidget(h2("Devices"))
        self.devs = QTableWidget(0, 4)
        self.devs.setHorizontalHeaderLabels(["Device", "Kind", "State", "Alt"])
        self.devs.verticalHeader().setVisible(False)
        self.devs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.devs.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.devs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.devs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.devs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.devs.cellDoubleClicked.connect(lambda r, _c: self._zoom_device(r))
        self.lay.addWidget(self.devs, 1)
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind.setCurrentText("uas")
        self.daff = QComboBox()
        self.daff.addItems(["friend", "hostile", "neutral", "unknown", "suspect"])
        self.lay.addWidget(row(self.kind, self.daff, button("Classify", self._classify,
                                                            tip="Operator sets what the selected track is")))
        self.lay.addWidget(row(button("Open video", self._video), button("Drop track", self._drop)))
        w.devices.connections_changed.connect(self.refresh_conns)
        w.devices.devices_changed.connect(self.refresh_devs)
        w.devices.devices_changed.connect(self.refresh_conns)       # keeps message counters current
        self._rebuild_form()
        self.refresh_conns()
        self.refresh_devs()

    def _apply_preset(self):
        name = self.preset.currentData()
        if not name:
            return
        p = PRESETS[name]
        self.transport.setCurrentIndex(self.transport.findData(p["transport"]))
        self.protocol.setCurrentIndex(self.protocol.findData(p["decoder"]))
        self._rebuild_form()
        for (scope, key), edit in self.fields.items():
            src = p.get("transport_opts" if scope == "t" else "decoder_opts", {})
            if key in src:
                edit.setText(str(src[key]))

    def _rebuild_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        for scope, cls in (("t", TRANSPORTS[self.transport.currentData()]),
                           ("d", DECODERS[self.protocol.currentData()])):
            for key, (default, help_text) in cls.options.items():
                e = QLineEdit(str(default))
                e.setToolTip(help_text)
                if key == "password":
                    e.setEchoMode(QLineEdit.EchoMode.Password)
                self.form.addRow(key.replace("_", " "), e)
                self.fields[(scope, key)] = e

    def _connect(self):
        t, d = self.transport.currentData(), self.protocol.currentData()
        prof = {"name": self.preset.currentData() or f"{DECODERS[d].label.split(' (')[0]} / {t}",
                "transport": t, "decoder": d,
                "transport_opts": {k: e.text() for (s, k), e in self.fields.items() if s == "t" and e.text()},
                "decoder_opts": {k: e.text() for (s, k), e in self.fields.items() if s == "d" and e.text()}}
        try:
            self.win.devices.add_connection(prof)
            self.win.say(f"Connection opened: {prof['name']}.")
        except Exception as exc:
            self.win.say(f"Could not open connection: {exc}", "error")

    def _conn_action(self, action):
        item = self.conns.currentItem()
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        mgr = self.win.devices
        try:
            {"start": mgr.start, "stop": mgr.stop, "remove": mgr.remove_connection}[action](cid)
        except Exception as exc:
            self.win.say(str(exc), "error")

    def refresh_conns(self):
        current = self.conns.currentItem().data(Qt.ItemDataRole.UserRole) if self.conns.currentItem() else None
        self.conns.clear()
        colors = {"listening": theme.OK, "connected": theme.OK, "polling": theme.OK, "reading": theme.OK,
                  "error": theme.BAD, "stopped": theme.MUTED, "finished": theme.MUTED}
        for c in self.win.devices.connections.values():
            it = QListWidgetItem(f"{c.profile['name']}\n   {c.state} {c.detail}  [{c.messages} msgs]")
            it.setForeground(QColor(colors.get(c.state, theme.WARN)))
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            self.conns.addItem(it)
            if c.id == current:
                self.conns.setCurrentItem(it)

    def refresh_devs(self):
        sel = self._selected_uid()
        devs = list(self.win.devices.devices.values())
        self.devs.setRowCount(len(devs))
        for r, d in enumerate(devs):
            vals = [d.alias or d.name, d.obs.kind, d.status, "" if d.obs.alt is None else f"{d.obs.alt:.0f} m"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, d.uid)
                if c == 2:
                    it.setForeground(QColor(theme.OK if d.status == "live" else theme.WARN))
                self.devs.setItem(r, c, it)
            if d.uid == sel:
                self.devs.selectRow(r)

    def _selected_uid(self):
        items = self.devs.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _zoom_device(self, r):
        uid = self.devs.item(r, 0).data(Qt.ItemDataRole.UserRole)
        d = self.win.devices.devices.get(uid)
        if d:
            self.win.wb.zoom_to(d.obs.lat, d.obs.lon, 2500)

    def _classify(self):
        uid = self._selected_uid()
        if uid:
            self.win.devices.configure_device(uid, kind=self.kind.currentText(), affiliation=self.daff.currentText())

    def _drop(self):
        uid = self._selected_uid()
        if uid:
            self.win.devices.remove_device(uid)

    def _video(self):
        uid = self._selected_uid()
        d = self.win.devices.devices.get(uid) if uid else None
        if d is None:
            return
        self.win.open_video(d)


# ---------------------------------------------------------------------------
class ToolsPanel(Panel):
    title = "All tools"
    stretch_at_end = False

    def build(self):
        idx = CapabilityIndex.instance()
        provs = ", ".join(f"{n} {p}" for p, n in sorted(idx.providers().items(), key=lambda x: -x[1]))
        self.lay.addWidget(hint(f"The complete QGIS engine: {len(idx)} algorithms ({provs}), grouped by what a "
                                "planner is trying to do."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools, e.g. buffer, contour, zonal...")
        self.search.textChanged.connect(self.refresh)
        self.lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._open)
        self.lay.addWidget(self.tree, 1)
        self.lay.addWidget(row(button("Open tool", lambda: self._open(self.tree.currentItem()), primary=True),
                               button("Ask BADR to run it", self._ask)))
        self.refresh()

    def refresh(self, *_):
        idx = CapabilityIndex.instance()
        self.tree.clear()
        q = self.search.text().strip()
        if q:
            for hit in idx.search(q, 40):
                it = QTreeWidgetItem([f"{hit['name']}   ·  {hit['provider']}"])
                it.setData(0, Qt.ItemDataRole.UserRole, hit["id"])
                it.setToolTip(0, hit["id"])
                self.tree.addTopLevelItem(it)
            return
        for flow, items in idx.by_workflow().items():
            top = QTreeWidgetItem([f"{flow}  ({len(items)})"])
            for a in items:
                it = QTreeWidgetItem([a["name"]])
                it.setData(0, Qt.ItemDataRole.UserRole, a["id"])
                it.setToolTip(0, f"{a['id']}  ·  {a['provider']} / {a['group']}")
                top.addChild(it)
            self.tree.addTopLevelItem(top)

    def _open(self, item, *_):
        alg_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if alg_id:
            self.win.open_algorithm(alg_id)

    def _ask(self):
        item = self.tree.currentItem()
        alg_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if alg_id:
            self.win.command.setText(f"Run {alg_id} on ")
            self.win.command.setFocus()


class DrawPanel(Panel):
    """Free drawing and custom layers - everything that is not a NATO symbol."""

    title = "Draw"

    def build(self):
        from ..core import exchange
        from .widgets import NewLayerDialog
        self._exchange, self._dialog = exchange, NewLayerDialog
        w = self.win
        self.lay.addWidget(hint("Draw into your own layers, or into feature classes you added from the Catalog. "
                                "Quick layers are saved with the operation; feature classes are saved in their "
                                "geodatabase."))
        self.lay.addWidget(h2("Layer"))
        self.layer = QComboBox()
        self.lay.addWidget(self.layer)
        self.lay.addWidget(row(button("New layer", self.new_layer, primary=True),
                               button("Table", lambda: w.open_table(self.current()),
                                      tip="Open the attribute table to view and edit rows"),
                               button("Colour", lambda: self.current() and w.recolor_layer(self.current()))))
        self.lay.addWidget(h2("Add a feature"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Label shown on the map")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes (optional)")
        self.lay.addWidget(self.name)
        self.lay.addWidget(self.notes)
        self.draw_btn = button("Draw on map", self.draw, primary=True)
        self.lay.addWidget(self.draw_btn)
        self.lay.addWidget(hint("Points: click. Lines and areas: click each vertex, right-click to finish, "
                                "Backspace removes the last vertex. Select a drawn feature to edit or delete it."))
        self.lay.addWidget(h2("Quick layers"))
        for label, geom, color in (("Notes (points)", "point", "#ffd24a"), ("Sketch lines", "line", "#ff8a3d"),
                                   ("Sketch areas", "polygon", "#b48cff")):
            self.lay.addWidget(button(label, lambda l=label, g=geom, c=color: self.quick(l, g, c)))
        w.wb.project.layersAdded.connect(lambda *_: self.refresh())
        w.wb.project.layersRemoved.connect(lambda *_: self.refresh())
        self.refresh()

    def on_operation(self):
        self.refresh()

    def refresh(self):
        try:
            current = self.layer.currentData()
        except RuntimeError:
            return                             # panel of a window that has been closed
        self.layer.blockSignals(True)
        self.layer.clear()
        for lyr in self._exchange.editable_layers(self.win.wb):
            kind = {0: "points", 1: "lines", 2: "areas"}.get(lyr.geometryType(), "")
            where = "  ·  geodatabase" if lyr.customProperty("badr/gdb") else ""
            self.layer.addItem(f"{lyr.name()}  ({kind}){where}", lyr.id())
        i = self.layer.findData(current)
        if i >= 0:
            self.layer.setCurrentIndex(i)
        self.layer.blockSignals(False)
        self.draw_btn.setEnabled(self.layer.count() > 0)

    def current(self):
        return self.win.wb.project.mapLayer(self.layer.currentData()) if self.layer.count() else None

    def _select(self, layer):
        self.refresh()
        i = self.layer.findData(layer.id())
        if i >= 0:
            self.layer.setCurrentIndex(i)

    def new_layer(self):
        dlg = self._dialog(self.win)
        if not dlg.exec():
            return
        name, geom, color, fields = dlg.values()
        try:
            self._select(self._exchange.create_user_layer(self.win.wb, name, geom, color, fields))
        except Exception as exc:
            self.win.say(str(exc), "error")

    def quick(self, label, geom, color):
        name = label.split(" (")[0]
        existing = next((l for l in self._exchange.user_layers(self.win.wb) if l.name() == name), None)
        self._select(existing or self._exchange.create_user_layer(self.win.wb, name, geom, color))
        self.draw()

    def draw(self):
        layer = self.current()
        if layer is None:
            return
        win, gt = self.win, layer.geometryType()
        color = layer.customProperty("badr/color") or "#ffd24a"

        def add(pts):
            try:
                self._exchange.add_user_feature(win.wb, layer, pts, self.name.text().strip(),
                                                self.notes.text().strip())
            except Exception as exc:
                win.say(str(exc), "error")
            if gt == 0:
                win.pick(1, add, f"{layer.name()}: click to add points (Esc to stop)")
        if gt == 0:
            win.pick(1, add, f"{layer.name()}: click to add points (Esc to stop)")
        else:
            win.sketch(gt == 2, add, f"{layer.name()}: click vertices, right-click to finish", color)


def _catalog_panel(win):
    from .catalog import CatalogPanel          # imported late: catalog.py builds on this module
    return CatalogPanel(win)


MODES = [
    ("CONTENTS", "picture", PicturePanel, "map layers in drawing order, attribute tables, export"),
    ("CATALOG", "catalog", _catalog_panel, "geodatabases, feature datasets, feature classes, domains"),
    ("PLAN", "plan", PlanPanel, "NATO symbols and control measures"),
    ("DRAW", "draw", DrawPanel, "draw into your own layers and feature classes"),
    ("TERRAIN", "terrain", TerrainPanel, "visibility, mobility, routes, landing zones"),
    ("INTEL", "intel", IntelPanel, "incidents and pattern analysis"),
    ("IMAGERY", "imagery", ImageryPanel, "Earth Engine imagery and change detection"),
    ("SENSORS", "sensors", SensorsPanel, "drones, trackers and sensor feeds"),
    ("TOOLS", "tools", ToolsPanel, "every QGIS tool, searchable"),
]
