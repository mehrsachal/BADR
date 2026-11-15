"""BADR 2 main window.

One screen, four regions:

    classification banner
    header      brand | command bar (the primary control) | Zulu clock | AI state
    body        mode rail + workflow panel | map | assistant feed + review queue
    status      cursor MGRS / lat-lon | elevation | scale | symbol engine
"""
import base64
import html
import json
import os
import re
import threading
from datetime import datetime, timezone

from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsMessageBar
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence
from qgis.PyQt.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QShortcut, QSplitter, QStackedWidget, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ..ai.agent import Agent
from ..ai.executor import IfaceShim
from ..ai.quick import route as route_quick
from ..ai.tools import Toolbox
from ..core import geodesy
from ..core.mgrs import format_dd, parse_coordinate, to_mgrs
from ..core.settings import PROVIDERS, Settings
from ..core.workbench import Workbench
from ..sensors.manager import DeviceManager
from ..symbology import sidc as S
from ..symbology.engine import SymbolEngine
from . import theme
from .map_tools import PickTool, SketchTool
from .panels import MODES, button, h2, hint, row

HELP = """<b>Type what you want.</b> Examples:<br>
&bull; <i>Put a hostile mortar team at 11S NU 5512 0345 and show 3 and 5 km range rings</i><br>
&bull; <i>What can an OP on the ridge north of OBJ FOX see? Use a 4 km radius</i><br>
&bull; <i>Find landing zones within 2 km of the objective</i><br>
&bull; <i>Where is the IED threat highest and which route is most exposed?</i><br>
&bull; <i>Add recent Sentinel-2 imagery and show change since August</i><br>
&bull; <i>Connect the drone on UDP 14550</i><br>
<br>A coordinate on its own (MGRS or lat, lon) jumps the map there. Shortcuts: <b>Ctrl+K</b> command bar,
<b>Esc</b> cancel map tool, right-click the map for actions at that point."""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BADR settings")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        for key, info in PROVIDERS.items():
            self.provider.addItem(info["label"], key)
        self.provider.setCurrentIndex(self.provider.findData(Settings.provider()))
        self.provider.currentIndexChanged.connect(self._load_provider)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.endpoint = QLineEdit()
        form.addRow("AI provider", self.provider)
        form.addRow("API key", self.key)
        form.addRow("Model", self.model)
        form.addRow("Endpoint", self.endpoint)
        self.effort = QComboBox()
        for key, label in (("low", "Fast - map actions and quick answers"), ("medium", "Balanced"),
                           ("high", "Deep - complex analysis (slower, more tokens)")):
            self.effort.addItem(label, key)
        self.effort.setCurrentIndex(max(0, self.effort.findData(Settings.get("ai/effort"))))
        form.addRow("Response depth", self.effort)
        self.review = QCheckBox("Hold AI-created features for my review before they join the picture")
        self.review.setChecked(bool(Settings.get("ai/review_ai_changes")))
        self.confirm = QCheckBox("Ask me before running AI-written code")
        self.confirm.setChecked(bool(Settings.get("ai/confirm_code")))
        form.addRow("", self.review)
        form.addRow("", self.confirm)
        self.gee = QLineEdit(Settings.get("gee/project") or "")
        self.gee.setPlaceholderText("Google Cloud project id registered for Earth Engine")
        form.addRow("Earth Engine project", self.gee)
        self.classification = QLineEdit(Settings.get("ui/classification"))
        form.addRow("Classification banner", self.classification)
        self.coord = QComboBox()
        self.coord.addItems(["MGRS", "Lat/Lon"])
        self.coord.setCurrentText(Settings.get("ui/coord_format"))
        form.addRow("Coordinates", self.coord)
        lay.addLayout(form)
        lay.addWidget(hint("Keys are stored in this user's local settings only and are sent only to the provider "
                           "you select. For air-gapped use choose Ollama or an on-prem OpenAI-compatible endpoint."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._load_provider()

    def _load_provider(self):
        p = self.provider.currentData()
        self.key.setText(Settings.get(f"ai/{p}/key", "") or "")
        self.key.setEnabled(PROVIDERS[p]["needs_key"] or p == "custom")
        self.model.clear()
        self.model.addItems(PROVIDERS[p]["models"])
        self.model.setCurrentText(Settings.model(p))
        self.endpoint.setText(Settings.endpoint(p))

    def _save(self):
        p = self.provider.currentData()
        Settings.set("ai/provider", p)
        Settings.set(f"ai/{p}/key", self.key.text().strip())
        Settings.set(f"ai/{p}/model", self.model.currentText().strip())
        Settings.set(f"ai/{p}/endpoint", self.endpoint.text().strip())
        Settings.set("ai/effort", self.effort.currentData())
        Settings.set("ai/review_ai_changes", self.review.isChecked())
        Settings.set("ai/confirm_code", self.confirm.isChecked())
        Settings.set("gee/project", self.gee.text().strip())
        Settings.set("ui/classification", self.classification.text().strip())
        Settings.set("ui/coord_format", self.coord.currentText())
        self.accept()


class MainWindow(QMainWindow):
    _agent_event = pyqtSignal(str, object)
    _invoke = pyqtSignal(object)

    def __init__(self, operation_folder=None):
        super().__init__()
        self.setWindowTitle("BADR 2")
        self.resize(1500, 920)
        QApplication.instance().setStyleSheet(theme.QSS)

        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(QColor("#0b1014"))
        self.canvas.enableAntiAliasing(True)
        self.wb = Workbench(self.canvas, self)
        self.wb.open_operation(operation_folder)
        self.devices = DeviceManager(self.wb, self)
        self.manual = Toolbox(self.wb, self.devices, confirm_code=lambda c, e: True, source="manual")
        self.ai_tools = Toolbox(self.wb, self.devices, confirm_code=self._confirm_code, source="ai")
        self.agent = Agent(self.ai_tools, invoke=self._invoke_on_gui,
                           on_event=lambda kind, data: self._agent_event.emit(kind, data))
        self._agent_event.connect(self._on_agent_event)
        self._invoke.connect(lambda job: job())
        self._busy = False
        self._sim = None
        self._history, self._hist_i = [], 0

        self._build()
        self.pan = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.pan)
        self.canvas.xyCoordinates.connect(self._on_cursor)
        self.canvas.scaleChanged.connect(lambda _s: self._update_scale())
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._context_menu)
        SymbolEngine.instance().rendered.connect(self.canvas.refreshAllLayers)
        self._sync_layers()
        self.wb.project.layerTreeRoot().visibilityChanged.connect(lambda *_: self._sync_layers())
        self.wb.project.layerTreeRoot().addedChildren.connect(lambda *_: QTimer.singleShot(0, self._sync_layers))
        self.wb.project.layerTreeRoot().removedChildren.connect(lambda *_: QTimer.singleShot(0, self._sync_layers))
        self.wb.project.layerTreeRoot().layerOrderChanged.connect(self._sync_layers)
        self._wire_operation()
        self.wb.zoom_to_bbox(-20, 5, 60, 60, margin=0)

        import qgis.utils
        qgis.utils.iface = IfaceShim(self.wb, self)

        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.command.setFocus(), self.command.selectAll()))
        QShortcut(QKeySequence("Escape"), self, activated=self.cancel_tool)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._tick()
        self.devices.load_saved(autostart=False)
        self.feed_html(HELP)
        self._update_ai_pill()

    # ---- layout ----------------------------------------------------------
    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.banner_top = QLabel()
        self.banner_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.banner_top)

        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 8, 14, 8)
        brand = QLabel("BADR")
        brand.setObjectName("brand")
        self.opname = QLabel("")
        self.opname.setObjectName("opname")
        self.command = QLineEdit()
        self.command.setObjectName("command")
        self.command.setPlaceholderText("Ask BADR or give an order...  (Ctrl+K)")
        self.command.returnPressed.connect(self._submit)
        self.command.installEventFilter(self)
        self.stop_btn = button("Stop", self._stop)
        self.stop_btn.setVisible(False)
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        self.ai_pill = QLabel()
        self.ai_pill.setObjectName("pill")
        gear = button("Settings", self.open_settings)
        gear.setObjectName("ghost")
        hl.addWidget(brand)
        hl.addSpacing(10)
        hl.addWidget(self.opname)
        hl.addStretch(1)
        hl.addWidget(self.ai_pill)
        hl.addWidget(self.clock)
        hl.addWidget(gear)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("rail")
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 6, 0, 6)
        rl.setSpacing(0)
        self.stack = QStackedWidget()
        self.mode_group = QButtonGroup(self)
        self.panels = {}
        for i, (name, glyph, cls, tip) in enumerate(MODES):
            b = QToolButton()
            b.setObjectName("mode")
            b.setText(f"{glyph}\n{name}")
            b.setToolTip(tip)
            b.setCheckable(True)
            self.mode_group.addButton(b, i)
            rl.addWidget(b)
            panel = cls(self)
            self.panels[name] = panel
            self.stack.addWidget(panel)
        rl.addStretch(1)
        self.mode_group.idClicked.connect(self.stack.setCurrentIndex)
        self.mode_group.button(0).setChecked(True)
        body.addWidget(rail)

        side = QFrame()
        side.setObjectName("side")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.stack)
        side.setMinimumWidth(318)
        side.setMaximumWidth(380)

        mapbox = QWidget()
        ml = QVBoxLayout(mapbox)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.message_bar = QgsMessageBar()
        self.tool_hint = QLabel("")
        self.tool_hint.setStyleSheet(f"background:{theme.WARN};color:#1a1200;font-weight:700;padding:5px 12px;")
        self.tool_hint.setVisible(False)
        ml.addWidget(self.message_bar)
        ml.addWidget(self.tool_hint)
        ml.addWidget(self.canvas, 1)

        assistant = QFrame()
        assistant.setObjectName("assistant")
        al = QVBoxLayout(assistant)
        al.setContentsMargins(12, 10, 12, 10)
        al.setSpacing(6)
        self.review_head = h2("Review queue")
        al.addWidget(self.review_head)
        self.review_list = QListWidget()
        self.review_list.setMaximumHeight(132)
        self.review_list.itemDoubleClicked.connect(self._zoom_review)
        al.addWidget(self.review_list)
        self.review_row = row(button("Accept", lambda: self._resolve(True), primary=True),
                              button("Reject", lambda: self._resolve(False)),
                              button("Accept all", self._accept_all))
        al.addWidget(self.review_row)
        al.addWidget(h2("Assistant"))
        self.feed = QTextBrowser()
        self.feed.setOpenExternalLinks(False)
        self.feed.setStyleSheet(f"QTextBrowser {{ background:{theme.BG}; border:1px solid {theme.LINE}; padding:6px; }}")
        al.addWidget(self.feed, 1)
        self.status_line = hint("")
        al.addWidget(self.status_line)
        # The chat bar lives here, under the conversation it drives.
        send = button("Send", self._submit, primary=True)
        al.addWidget(row(self.command, send, self.stop_btn))
        al.setStretchFactor(self.feed, 1)
        quick = row(button("Brief me", lambda: self.ask(
            "Give me a short situation brief from the current picture: threat, friendly, terrain, key risks.")),
            button("Help", lambda: self.feed_html(HELP)), button("Clear", self._clear_chat))
        al.addWidget(quick)
        assistant.setMinimumWidth(340)
        assistant.setMaximumWidth(460)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(side)
        split.addWidget(mapbox)
        split.addWidget(assistant)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 810, 360])
        split.setHandleWidth(1)
        body.addWidget(split, 1)
        root.addLayout(body, 1)

        status = QFrame()
        status.setObjectName("statusbar")
        st = QHBoxLayout(status)
        st.setContentsMargins(12, 3, 12, 3)
        self.coord_label = QLabel("--")
        self.elev_label = QLabel("")
        self.scale_label = QLabel("")
        self.engine_label = QLabel(f"symbology: {SymbolEngine.instance().backend}")
        st.addWidget(self.coord_label)
        st.addSpacing(24)
        st.addWidget(self.elev_label)
        st.addStretch(1)
        st.addWidget(self.engine_label)
        st.addSpacing(24)
        st.addWidget(self.scale_label)
        root.addWidget(status)
        self.banner_bottom = QLabel()
        self.banner_bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.banner_bottom)
        self.setCentralWidget(central)
        self._apply_classification()

    def _apply_classification(self):
        text = Settings.get("ui/classification") or ""
        for lab in (self.banner_top, self.banner_bottom):
            lab.setText(text)
            lab.setStyleSheet(theme.classification_style(text))
            lab.setVisible(bool(text))

    def eventFilter(self, obj, ev):
        if obj is self.command and ev.type() == ev.Type.KeyPress and self._history:
            if ev.key() == Qt.Key.Key_Up:
                self._hist_i = max(0, self._hist_i - 1)
                self.command.setText(self._history[self._hist_i])
                return True
            if ev.key() == Qt.Key.Key_Down:
                self._hist_i = min(len(self._history), self._hist_i + 1)
                self.command.setText(self._history[self._hist_i] if self._hist_i < len(self._history) else "")
                return True
        return super().eventFilter(obj, ev)

    # ---- project / layers ------------------------------------------------
    def _sync_layers(self):
        root = self.wb.project.layerTreeRoot()
        self.canvas.setLayers([n.layer() for n in root.findLayers() if n.isVisible() and n.layer()])
        self.canvas.setDestinationCrs(self.wb.project.crs())
        self.canvas.refresh()

    def _wire_operation(self):
        op = self.wb.operation
        self.opname.setText(op.name)
        op.review_changed.connect(self._refresh_review)
        op.changed.connect(lambda _k: self.canvas.refresh())
        self._refresh_review()
        for p in self.panels.values():
            p.on_operation()

    def new_operation(self):
        name, ok = QInputDialog.getText(self, "New operation", "Operation name:")
        if not ok or not name.strip():
            return
        folder = os.path.join(Settings.get("workspace"), re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()))
        self._open(folder, name.strip())

    def open_operation_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Open operation folder", Settings.get("workspace"))
        if folder:
            self._open(folder)

    def _open(self, folder, name=None):
        ext = self.wb.extent_wgs84()
        self.wb.open_operation(folder, name)
        self._sync_layers()
        self._wire_operation()
        self.agent.reset()
        if ext:
            self.wb.zoom_to_bbox(*ext, margin=0)
        self.say(f"Operation '{self.wb.operation.name}' opened ({folder}).")

    def add_file_layer(self):
        from qgis.core import QgsRasterLayer, QgsVectorLayer
        paths, _ = QFileDialog.getOpenFileNames(self, "Add GIS data", "", "GIS data (*.*)")
        for path in paths:
            name = os.path.splitext(os.path.basename(path))[0]
            layer = QgsVectorLayer(path, name, "ogr")
            if not layer.isValid():
                layer = QgsRasterLayer(path, name)
            if not layer.isValid():
                self.say(f"Could not read {os.path.basename(path)}.", "error")
                continue
            self.wb.add_layer(layer, "Reference")
            self.wb.zoom_to_layer(layer)

    def export_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export map image", "badr_map.png", "PNG (*.png)")
        if path:
            self.canvas.saveAsImage(path)
            self.say(f"Map exported to {path} (with world file).")

    def open_settings(self):
        if SettingsDialog(self).exec():
            self._apply_classification()
            self.agent.reset()
            self._update_ai_pill()

    def open_algorithm(self, alg_id):
        """Any QGIS algorithm, through QGIS' own parameter dialog."""
        try:
            import processing
            processing.execAlgorithmDialog(alg_id, {})
            self._sync_layers()
        except Exception as exc:
            self.say(f"Could not open {alg_id}: {exc}. You can still ask BADR to run it.", "warn")

    def open_video(self, device):
        url = device.video_url
        if not url:
            url, ok = QInputDialog.getText(self, "Video stream", f"Stream URL for {device.name} (RTSP / HLS / HTTP):")
            if not ok or not url.strip():
                return
            self.devices.configure_device(device.uid, video_url=url.strip())
        try:
            from .video import VideoWindow
            self._video = VideoWindow(url.strip(), device.alias or device.name, self)
            self._video.show()
        except Exception as exc:
            self.say(f"Video playback unavailable ({exc}). Stream URL: {url}", "warn")

    # ---- map tools -------------------------------------------------------
    def _arm(self, tool, callback, text):
        self.cancel_tool()
        tool.finished.connect(lambda pts: (self.cancel_tool(), callback(pts)))
        tool.cancelled.connect(self.cancel_tool)
        self._tool = tool
        self.canvas.setMapTool(tool)
        self.tool_hint.setText(text)
        self.tool_hint.setVisible(True)
        self.canvas.setFocus()

    def pick(self, count, callback, text):
        self._arm(PickTool(self.canvas, self.wb, count), callback, text)

    def sketch(self, polygon, callback, text, color="#00a8dc"):
        self._arm(SketchTool(self.canvas, self.wb, polygon, color), callback, text)

    def cancel_tool(self):
        self.tool_hint.setVisible(False)
        if self.canvas.mapTool() is not self.pan:
            self.canvas.setMapTool(self.pan)
        self._tool = None

    def _to_wgs(self, map_point):
        tr = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.wb.wgs84, self.wb.project)
        p = tr.transform(map_point)
        return p.y(), p.x()

    def _on_cursor(self, map_point):
        try:
            lat, lon = self._to_wgs(map_point)
        except Exception:
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return
        self._cursor = (lat, lon)
        if Settings.get("ui/coord_format") == "MGRS":
            self.coord_label.setText(f"{to_mgrs(lat, lon, 5)}    {format_dd(lat, lon)}")
        else:
            self.coord_label.setText(f"{format_dd(lat, lon)}    {to_mgrs(lat, lon, 5)}")
        dem = self.wb.dem()
        if dem is not None:
            tr = QgsCoordinateTransform(self.wb.wgs84, dem.crs(), self.wb.project)
            val, ok = dem.dataProvider().sample(tr.transform(QgsPointXY(lon, lat)), 1)
            self.elev_label.setText(f"elev {val:.0f} m" if ok and val == val else "")

    def _update_scale(self):
        self.scale_label.setText(f"1:{self.canvas.scale():,.0f}")

    def _nearest(self, lat, lon):
        """Nearest COP feature within ~12 px of a point -> (key, feature) or (None, None)."""
        tol = self.canvas.mapUnitsPerPixel() * 12
        tr = QgsCoordinateTransform(self.wb.wgs84, self.canvas.mapSettings().destinationCrs(), self.wb.project)
        mp = tr.transform(QgsPointXY(lon, lat))
        rect_m = QgsRectangle(mp.x() - tol, mp.y() - tol, mp.x() + tol, mp.y() + tol)
        rect = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.wb.wgs84,
                                      self.wb.project).transformBoundingBox(rect_m)
        from qgis.core import QgsFeatureRequest, QgsGeometry
        probe = QgsGeometry.fromRect(rect)
        for key in ("units", "incidents", "detections", "lines", "areas"):
            layer = self.wb.operation.layer(key)
            for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                g = f.geometry()
                if key == "areas":
                    if g.convertToType(1, False).intersects(probe) or g.area() < probe.area() * 50 and g.intersects(probe):
                        return key, f
                elif g.intersects(probe):
                    return key, f
        return None, None

    def _context_menu(self, pos):
        lat, lon = self._to_wgs(self.canvas.getCoordinateTransform().toMapCoordinates(pos))
        here = [lat, lon]
        grid = to_mgrs(lat, lon, 4)
        m = QMenu(self)
        key, feat = self._nearest(lat, lon)
        if feat is not None:
            names = self.wb.operation.layer(key).fields().names()
            label = (feat["name"] if "name" in names else "") or (S.describe(feat["sidc"]) if "sidc" in names and feat["sidc"] else key)
            uid = feat["uid"]
            m.addAction(f"Rename '{label}'...", lambda: self._rename(key, uid))
            if "sidc" in names:
                sub = m.addMenu("Set affiliation")
                for a in ("friend", "hostile", "neutral", "unknown", "suspect"):
                    sub.addAction(a.capitalize(), lambda a=a: self.do("update_feature", uid=uid, affiliation=a, quiet=True))
            if "review" in names and feat["review"] == "pending":
                m.addAction("Accept (confirm)", lambda: self.wb.operation.resolve(key, uid, True))
            m.addAction(f"Delete '{label}'", lambda: self.do("delete_feature", uid=uid, quiet=True))
            m.addAction("Ask BADR about this", lambda: self.ask(f"Tell me about the feature with uid {uid} and what is around it."))
            m.addSeparator()
        m.addAction(f"Copy {grid}", lambda: QApplication.clipboard().setText(grid))
        m.addAction("Viewshed from here", lambda: self.do("terrain", analysis="viewshed", location=here))
        m.addAction("Range rings 1 / 3 / 5 km", lambda: self.do("terrain", analysis="range_rings", location=here,
                                                               radii_m=[1000, 3000, 5000]))
        m.addAction("Cordon here", lambda: self.do("terrain", analysis="cordon", location=here))
        m.addAction("Ask BADR about this location", lambda: self._ask_about(grid))
        m.exec(self.canvas.mapToGlobal(pos))

    def _rename(self, key, uid):
        text, ok = QInputDialog.getText(self, "Rename", "Designation:")
        if ok:
            self.wb.operation.update(key, uid, {"name": text.strip()})

    def _ask_about(self, grid):
        self.command.setText(f"At {grid}: ")
        self.command.setFocus()

    # ---- manual actions (same Toolbox the AI uses) -------------------------
    def do(self, tool, quiet=False, **args):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status_line.setText(f"Running {tool}...")
        QApplication.processEvents()
        try:
            text, err = self.manual.call(tool, {k: v for k, v in args.items() if v not in (None, "")})
        finally:
            QApplication.restoreOverrideCursor()
            self.status_line.setText("")
        if err:
            self.say(text, "error")
            return None
        try:
            data = json.loads(text)
            msg = data.get("summary") if isinstance(data, dict) else None
        except ValueError:
            data, msg = text, text
        if msg and not quiet:
            self.say(msg)
        self._sync_layers()
        return data

    # ---- assistant ---------------------------------------------------------
    def feed_html(self, fragment):
        self.feed.append(f"<div style='margin:3px 0'>{fragment}</div>")
        self.feed.verticalScrollBar().setValue(self.feed.verticalScrollBar().maximum())

    def say(self, text, level="info"):
        color = {"info": theme.TEXT, "warn": theme.WARN, "error": theme.BAD}[level]
        self.feed_html(f"<span style='color:{color}'>{html.escape(str(text))}</span>")

    @staticmethod
    def _md(text):
        t = html.escape(text)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"(?m)^#{1,4}\s*(.+)$", r"<b>\1</b>", t)
        t = re.sub(r"(?m)^\s*[-*]\s+", "&bull; ", t)
        return t.replace("\n", "<br>")

    def _update_ai_pill(self):
        if self._busy:
            self.ai_pill.setText("AI working")
            self.ai_pill.setStyleSheet(f"background:{theme.ACCENT};color:#06131c;")
        elif Settings.ai_ready():
            self.ai_pill.setText(f"AI ready · {Settings.model()}")
            self.ai_pill.setStyleSheet(f"background:#12351f;color:{theme.OK};")
        else:
            self.ai_pill.setText("AI not configured")
            self.ai_pill.setStyleSheet(f"background:#3a2c08;color:{theme.WARN};")

    def _submit(self):
        text = self.command.text().strip()
        if not text:
            return
        self.command.clear()
        self._history.append(text)
        self._hist_i = len(self._history)
        self.ask(text)

    def ask(self, text):
        """Route a command: bare coordinates and /help locally, everything else to the agent."""
        if text.lower() in ("/help", "help", "?"):
            self.feed_html(HELP)
            return
        # Fast path: routine orders are executed directly - no model call, no tokens.
        quick = route_quick(text)
        if quick:
            self.feed_html(f"<div style='color:{theme.ACCENT};font-weight:600;margin-top:8px'>&rsaquo; "
                           f"{html.escape(text)} <span style='color:{theme.MUTED};font-weight:400'>"
                           f"&middot; direct</span></div>")
            out = self.do(quick[0], **quick[1])
            if isinstance(out, str):
                self.say(out)
            return
        if self._busy:
            self.say("Still working on the previous request (Stop to cancel).", "warn")
            return
        self.feed_html(f"<div style='color:{theme.ACCENT};font-weight:600;margin-top:8px'>&rsaquo; {html.escape(text)}</div>")
        if not Settings.ai_ready():
            self.say("The assistant needs a model: open Settings and add an API key, or select a local model "
                     "(Ollama / on-prem endpoint). Every panel on the left works without it.", "warn")
            return
        self._busy = True
        self.stop_btn.setVisible(True)
        self._update_ai_pill()
        image = self._snapshot() if re.search(r"\b(see|look|screen|image|picture|this map|visible on)\b", text, re.I) else None
        threading.Thread(target=self.agent.run, args=(text, image), daemon=True).start()

    def _snapshot(self):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        self.canvas.grab().scaledToWidth(1100, Qt.TransformationMode.SmoothTransformation).save(buf, "PNG")
        return base64.b64encode(bytes(ba)).decode("ascii")

    def _stop(self):
        self.agent.abort()
        self.status_line.setText("Stopping after the current step...")

    def _invoke_on_gui(self, fn):
        """Called on the agent thread: run ``fn`` on the GUI thread and wait for it."""
        done = threading.Event()
        box = {}

        def job():
            try:
                box["value"] = fn()
            except Exception as exc:
                box["value"] = (f"{type(exc).__name__}: {exc}", True)
            finally:
                done.set()
        self._invoke.emit(job)
        done.wait()
        return box["value"]

    def _confirm_code(self, code, explanation):
        dlg = QDialog(self)
        dlg.setWindowTitle("Run AI-written code?")
        dlg.resize(640, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"<b>BADR wants to run code:</b> {html.escape(explanation or '')}"))
        ed = QPlainTextEdit(code)
        ed.setReadOnly(True)
        lay.addWidget(ed)
        bb = QDialogButtonBox()
        bb.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("Don't run", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        return bool(dlg.exec())

    def _on_agent_event(self, kind, data):
        if kind == "status":
            self.status_line.setText(f"{data}...")
        elif kind == "note":
            self.feed_html(f"<span style='color:{theme.MUTED}'>{self._md(data)}</span>")
        elif kind == "tool":
            args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in data["args"].items())
            self.status_line.setText(f"{data['name']}...")
            self.feed_html(f"<span style='color:{theme.MUTED};font-family:monospace;font-size:11px'>"
                           f"&#9656; {html.escape(data['name'])}({html.escape(args[:160])})</span>")
        elif kind == "tool_result":
            self._sync_layers()
            if not data["ok"]:
                self.feed_html(f"<span style='color:{theme.WARN};font-size:11px'>&nbsp;&nbsp;"
                               f"{html.escape(data['text'][:200])}</span>")
        elif kind == "usage":
            self._usage_note = (f"{data['calls']} model call(s) &middot; {data['in']:,} in"
                                f"{' + ' + format(data['cached'], ',') + ' cached' if data['cached'] else ''}"
                                f" &middot; {data['out']:,} out tokens")
        elif kind in ("final", "error"):
            if kind == "final" and getattr(self, "_usage_note", ""):
                data = data or ""
                note, self._usage_note = self._usage_note, ""
                if data:
                    self.feed_html(self._md(data))
                self.feed_html(f"<span style='color:{theme.MUTED};font-size:10px'>{note}</span>")
                data = ""
            if kind == "final" and data:
                self.feed_html(self._md(data))
            elif kind == "error":
                self.say(data, "error")
            self._busy = False
            self.stop_btn.setVisible(False)
            self.status_line.setText("")
            self._update_ai_pill()
            self._sync_layers()

    def _clear_chat(self):
        self.feed.clear()
        self.agent.reset()
        self.feed_html(HELP)

    # ---- review queue ------------------------------------------------------
    def _refresh_review(self):
        self.review_list.clear()
        pending = self.wb.operation.pending()
        for key, f in pending:
            names = self.wb.operation.layer(key).fields().names()
            what = S.describe(f["sidc"]) if "sidc" in names and f["sidc"] else (f["kind"] if "kind" in names else key)
            label = (f["name"] if "name" in names else f["label"]) or ""
            it = QListWidgetItem(f"{what}  {label}".strip())
            it.setData(Qt.ItemDataRole.UserRole, (key, f["uid"]))
            if "sidc" in names and f["sidc"]:
                from qgis.PyQt.QtGui import QIcon
                it.setIcon(QIcon(SymbolEngine.instance().pixmap(f["sidc"], 26)))
            self.review_list.addItem(it)
        n = len(pending)
        self.review_head.setText(f"REVIEW QUEUE  ·  {n} PENDING" if n else "REVIEW QUEUE  ·  CLEAR")
        self.review_head.setStyleSheet(f"color:{theme.WARN};" if n else "")
        self.review_list.setVisible(n > 0)
        self.review_row.setVisible(n > 0)

    def _resolve(self, accept):
        it = self.review_list.currentItem() or self.review_list.item(0)
        if it:
            key, uid = it.data(Qt.ItemDataRole.UserRole)
            self.wb.operation.resolve(key, uid, accept)

    def _accept_all(self):
        for key, f in self.wb.operation.pending():
            self.wb.operation.resolve(key, f["uid"], True)

    def _zoom_review(self, it):
        key, uid = it.data(Qt.ItemDataRole.UserRole)
        f = self.wb.operation.find(key, uid)
        if f is not None:
            p = f.geometry().centroid().asPoint()
            self.wb.zoom_to(p.y(), p.x(), 2500)

    # ---- simulator & housekeeping ------------------------------------------
    def toggle_simulator(self):
        from ..sensors.simulator import Simulator
        if self._sim:
            self._sim.stop()
            self._sim = None
            self.say("Training feed stopped.")
            return
        c = self.wb.center_wgs84() or (35.27, -116.68)
        self._sim = Simulator(self.devices, c[0], c[1])
        self._sim.start()
        self.say("Training feed started: a UAS orbit (MAVLink) and a patrol (CoT) on loopback UDP.")

    def _tick(self):
        now = datetime.now(timezone.utc)
        self.clock.setText(now.strftime("%d%H%MZ %b %y").upper())

    def closeEvent(self, e):
        if self._sim:
            self._sim.stop()
        self.devices.shutdown()
        super().closeEvent(e)
