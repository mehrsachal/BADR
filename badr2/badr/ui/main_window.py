"""BADR 2 main window.

    classification banner
    header   name | operation | activity indicator | assistant state | clock | settings
    body     mode rail + workflow panel | map (+ map toolbar) | inspector, review queue, assistant
    status   cursor MGRS / lat-lon | elevation | measure / hints | scale
"""
import base64
import html
import json
import math
import os
import re
import threading
from datetime import datetime, timezone

from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsMapLayer, QgsPointXY, QgsRectangle, QgsWkbTypes,
)
from qgis.gui import QgsLayerTreeViewMenuProvider, QgsMapCanvas, QgsMessageBar, QgsRubberBand
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QKeySequence
from qgis.PyQt.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QPlainTextEdit, QPushButton, QShortcut, QSplitter, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

from ..ai.agent import Agent
from ..ai.executor import IfaceShim
from ..ai.quick import route as route_quick, small_talk
from ..ai.tools import Toolbox
from ..core import exchange, geodesy
from ..core.mgrs import format_dd, to_mgrs
from ..core.settings import PROVIDERS, Settings
from ..core.workbench import Workbench
from ..sensors.manager import DeviceManager
from ..symbology import sidc as S
from ..symbology.engine import SymbolEngine
from . import icons, theme
from .map_tools import MeasureTool, PickTool, SelectTool, SketchTool
from .panels import MODES, button, h2, hint, row
from .widgets import (
    AttributeTableDialog, BusyIndicator, ExportDialog, Inspector, StoryPlayer, tool_button,
)

HELP = ("Type an order or a question. Examples: <i>hostile mortar team at 11S NU 5512 0345</i> &middot; "
        "<i>viewshed from OP 1, 4 km</i> &middot; <i>which route is most exposed to IEDs?</i><br>"
        "<span style='color:#8f9199'>Click selects, drag pans, Shift+drag box-selects, right-click for actions. "
        "Ctrl+K focuses this box.</span>")

SELECT_ORDER = ("tracks", "units", "incidents", "detections")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(470)
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
        for key, label in (("low", "Fast (recommended)"), ("medium", "Balanced"),
                           ("high", "Deep - slower, more tokens")):
            self.effort.addItem(label, key)
        self.effort.setCurrentIndex(max(0, self.effort.findData(Settings.get("ai/effort"))))
        form.addRow("Response depth", self.effort)
        self.review = QCheckBox("Hold AI-created features for my review")
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
        lay.addWidget(hint("The first model in each list is the fastest and cheapest. Keys stay in this user's "
                           "local settings and go only to the provider you select. For air-gapped use choose "
                           "Ollama or an on-prem endpoint."))
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


class LayerMenu(QgsLayerTreeViewMenuProvider):
    """Right-click menu of the layer list."""

    def __init__(self, win, view):
        super().__init__()
        self.win, self.view = win, view

    def createContextMenu(self):
        layer = self.view.currentLayer()
        if layer is None:
            return None
        win = self.win
        m = QMenu()
        m.addAction("Zoom to layer", lambda: win.wb.zoom_to_layer(layer))
        if layer.type() == QgsMapLayer.VectorLayer:
            m.addAction("Open attribute table", lambda: win.open_table(layer))
            m.addAction("Select all", layer.selectAll)
            m.addAction("Clear selection", layer.removeSelection)
        m.addAction("Export...", lambda: win.export_data([layer.id()]))
        m.addSeparator()
        if layer.customProperty("badr/user"):
            m.addAction("Change colour...", lambda: win.recolor_layer(layer))
        cop = set(win.wb.operation.layers.values())
        if layer not in cop:
            m.addAction("Rename...", lambda: win.rename_layer(layer))
            m.addAction("Remove", lambda: win.wb.project.removeMapLayer(layer.id()))
        return m


class LazyPanels:
    """Panels are built the first time they are opened, which keeps start-up fast."""

    def __init__(self, win, stack):
        self.win, self.stack, self.made = win, stack, {}
        self.names = [m[0] for m in MODES]

    def __getitem__(self, name):
        if name not in self.made:
            i = self.names.index(name)
            panel = MODES[i][2](self.win)
            holder = self.stack.widget(i)
            holder.layout().addWidget(panel)
            self.made[name] = panel
        return self.made[name]

    def values(self):
        return list(self.made.values())


class MainWindow(QMainWindow):
    _agent_event = pyqtSignal(str, object)
    _invoke = pyqtSignal(object)

    def __init__(self, operation_folder=None, progress=None):
        super().__init__()
        progress = progress or (lambda text, pct: None)
        self.setWindowTitle("BADR 2")
        self.resize(1500, 920)
        if not QApplication.instance().styleSheet():
            QApplication.instance().setStyleSheet(theme.QSS)
        Settings.migrate()

        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(QColor("#101113"))
        self.canvas.enableAntiAliasing(True)
        # Per-layer render cache + parallel rendering: a moving track or a new
        # symbol redraws one layer, not the terrain and imagery under it.
        self.canvas.setCachingEnabled(True)
        self.canvas.setParallelRenderingEnabled(True)
        self.canvas.setPreviewJobsEnabled(True)
        self.canvas.setMapUpdateInterval(400)
        self.canvas.setSelectionColor(QColor(theme.ACCENT))
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
        self._ai_token = None
        self._sim = None
        self._tool = None
        self._history, self._hist_i = [], 0
        self._selection = []            # [(layer, fid)]
        self._bands = []
        self._tables = {}
        self._fly = None
        self._cursor = None

        progress("Building the interface", 70)
        self._build()
        self.select_tool = SelectTool(self.canvas)
        self.select_tool.clicked.connect(self._select_at)
        self.select_tool.boxed.connect(self._select_box)
        self.select_tool.context.connect(self._context_menu)
        self.canvas.setMapTool(self.select_tool)
        self.canvas.xyCoordinates.connect(self._cursor_moved)
        self.canvas.scaleChanged.connect(lambda _s: self._update_scale())
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(70)
        self._cursor_timer.timeout.connect(self._show_cursor)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self._sync_layers_now)
        SymbolEngine.instance().rendered.connect(self._symbols_ready)
        root = self.wb.project.layerTreeRoot()
        root.visibilityChanged.connect(lambda *_: self._sync_layers())
        root.addedChildren.connect(lambda *_: self._sync_layers())
        root.removedChildren.connect(lambda *_: self._sync_layers())
        root.layerOrderChanged.connect(self._sync_layers)
        self._sync_layers_now()
        self._wire_operation()
        self.wb.zoom_to_bbox(-20, 5, 60, 60, margin=0)

        import qgis.utils
        qgis.utils.iface = IfaceShim(self.wb, self)

        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.command.setFocus(), self.command.selectAll()))
        QShortcut(QKeySequence("Escape"), self, activated=self.cancel_tool)
        QShortcut(QKeySequence("Delete"), self.canvas, activated=self.delete_selected)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._tick()
        self.devices.load_saved(autostart=False)
        self.feed_html(HELP)
        self._update_ai_state()

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
        header.setFixedHeight(34)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(10)
        brand = QLabel("BADR")
        brand.setObjectName("brand")
        self.opname = QLabel("")
        self.opname.setObjectName("opname")
        self.busy = BusyIndicator()
        self.ai_state = QLabel()
        self.ai_state.setObjectName("state")
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        hl.addWidget(brand)
        hl.addWidget(self.opname)
        hl.addStretch(1)
        hl.addWidget(self.busy)
        hl.addWidget(self.ai_state)
        hl.addWidget(self.clock)
        hl.addWidget(tool_button("settings", "Settings", self.open_settings))
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("rail")
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 4, 0, 4)
        rl.setSpacing(0)
        self.stack = QStackedWidget()
        self.mode_group = QButtonGroup(self)
        for i, (name, icon_name, _cls, tip) in enumerate(MODES):
            b = QToolButton()
            b.setObjectName("mode")
            b.setIcon(icons.icon(icon_name, "#8f9199", "#ffffff"))
            b.setToolTip(f"{name.capitalize()} - {tip}")
            b.setCheckable(True)
            self.mode_group.addButton(b, i)
            rl.addWidget(b)
            holder = QWidget()
            holder.setObjectName("bare")
            hlay = QVBoxLayout(holder)
            hlay.setContentsMargins(0, 0, 0, 0)
            self.stack.addWidget(holder)
        rl.addStretch(1)
        self.panels = LazyPanels(self, self.stack)
        self.mode_group.idClicked.connect(self.show_mode)
        body.addWidget(rail)

        side = QFrame()
        side.setObjectName("side")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.stack)
        side.setMinimumWidth(300)
        side.setMaximumWidth(380)

        mapbox = QWidget()
        ml = QVBoxLayout(mapbox)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.message_bar = QgsMessageBar()
        self.tool_hint = QLabel("")
        self.tool_hint.setStyleSheet(f"background:{theme.RAISED};color:{theme.TEXT};padding:4px 10px;"
                                     f"border-bottom:1px solid {theme.WARN};")
        self.tool_hint.setVisible(False)
        ml.addWidget(self.message_bar)
        ml.addWidget(self.tool_hint)
        ml.addWidget(self.canvas, 1)

        bar = QFrame(self.canvas)
        bar.setObjectName("maptools")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(3, 3, 3, 3)
        bl.setSpacing(2)
        self.btn_select = tool_button("select", "Select and explore: click a feature, drag to pan, Shift+drag to "
                                      "box-select (Esc)", self.cancel_tool, checkable=True)
        self.btn_select.setChecked(True)
        self.btn_measure = tool_button("measure", "Measure distance and bearing", self.start_measure, checkable=True)
        bl.addWidget(self.btn_select)
        bl.addWidget(self.btn_measure)
        bl.addWidget(tool_button("zoom_in", "Zoom in", self.canvas.zoomIn))
        bl.addWidget(tool_button("zoom_out", "Zoom out", self.canvas.zoomOut))
        bl.addWidget(tool_button("extent", "Zoom to the whole picture", self.zoom_to_picture))
        bl.addWidget(tool_button("table", "Attribute table of the selected layer", self.open_current_table))
        bl.addWidget(tool_button("export", "Export data to GIS formats", self.export_data))
        bar.move(10, 10)
        bar.adjustSize()
        self.map_toolbar = bar

        assistant = QFrame()
        assistant.setObjectName("assistant")
        al = QVBoxLayout(assistant)
        al.setContentsMargins(10, 8, 10, 8)
        al.setSpacing(6)
        self.inspector = Inspector()
        self.inspector.request_zoom.connect(self.zoom_to_selection)
        self.inspector.request_delete.connect(self.delete_selected)
        self.inspector.request_review.connect(self._review_selected)
        self.inspector.changed.connect(self._inspector_edited)
        al.addWidget(self.inspector)

        self.review_box = QWidget()
        self.review_box.setObjectName("bare")
        rb = QVBoxLayout(self.review_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(4)
        self.review_head = h2("Review queue")
        rb.addWidget(self.review_head)
        self.review_list = QListWidget()
        self.review_list.setMaximumHeight(72)
        self.review_list.itemClicked.connect(self._review_clicked)
        rb.addWidget(self.review_list)
        self.btn_accept = button("Accept", lambda: self._resolve(True), primary=True)
        self.btn_reject = button("Reject", lambda: self._resolve(False))
        self.btn_accept_all = button("Accept all", self._accept_all)
        rb.addWidget(row(self.btn_accept, self.btn_reject, self.btn_accept_all))
        al.addWidget(self.review_box)

        al.addWidget(h2("Assistant"))
        self.feed = QTextBrowser()
        self.feed.setOpenExternalLinks(False)
        self.feed.setMinimumHeight(84)
        al.addWidget(self.feed, 1)
        self.story = StoryPlayer()
        self.story.focus.connect(self._story_focus)
        al.addWidget(self.story)
        self.ai_busy = BusyIndicator()
        al.addWidget(self.ai_busy)
        self.command = QLineEdit()
        self.command.setObjectName("command")
        self.command.setPlaceholderText("Order or question  (Ctrl+K)")
        self.command.returnPressed.connect(self._submit)
        self.command.installEventFilter(self)
        self.send_btn = tool_button("send", "Send (Enter)", self._submit, size=30)
        self.stop_btn = tool_button("stop", "Stop the assistant", self._stop, size=30)
        self.stop_btn.setVisible(False)
        al.addWidget(row(self.command, self.send_btn, self.stop_btn))
        brief = button("Brief me", lambda: self.ask("Brief me on the current picture."))
        clear = button("Clear", self._clear_chat)
        for b in (brief, clear):
            b.setObjectName("flat")
        al.addWidget(row(brief, clear))
        assistant.setMinimumWidth(330)
        assistant.setMaximumWidth(450)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(side)
        split.addWidget(mapbox)
        split.addWidget(assistant)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 830, 350])
        split.setHandleWidth(1)
        body.addWidget(split, 1)
        root.addLayout(body, 1)

        status = QFrame()
        status.setObjectName("statusbar")
        status.setFixedHeight(22)
        st = QHBoxLayout(status)
        st.setContentsMargins(12, 0, 12, 0)
        self.coord_label = QLabel("")
        self.elev_label = QLabel("")
        self.info_label = QLabel("")
        self.scale_label = QLabel("")
        st.addWidget(self.coord_label)
        st.addSpacing(20)
        st.addWidget(self.elev_label)
        st.addStretch(1)
        st.addWidget(self.info_label)
        st.addSpacing(20)
        st.addWidget(self.scale_label)
        root.addWidget(status)
        self.banner_bottom = QLabel()
        self.banner_bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.banner_bottom)
        self.setCentralWidget(central)
        self._apply_classification()
        self.show_mode(0)

    def show_mode(self, i):
        self.mode_group.button(i).setChecked(True)
        self.panels[MODES[i][0]]          # build on first use
        self.stack.setCurrentIndex(i)

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
    def _sync_layers(self, *_):
        self._sync_timer.start()             # coalesce bursts of layer-tree changes into one update

    def _sync_layers_now(self):
        root = self.wb.project.layerTreeRoot()
        self.canvas.setLayers([n.layer() for n in root.findLayers() if n.isVisible() and n.layer()])
        self.canvas.setDestinationCrs(self.wb.project.crs())
        self.canvas.refresh()

    def _symbols_ready(self):
        try:
            op = self.wb.operation
            for key in ("units", "incidents", "detections", "tracks"):
                op.layer(key).triggerRepaint()
        except RuntimeError:
            pass                               # layers of an operation that was just closed

    def _wire_operation(self):
        op = self.wb.operation
        self.opname.setText(op.name)
        op.review_changed.connect(self._refresh_review)
        self._refresh_review()
        self.clear_selection()
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
        self._sync_layers_now()
        self._wire_operation()
        self.agent.reset()
        if ext:
            self.wb.zoom_to_bbox(*ext, margin=0)
        self.say(f"Operation '{self.wb.operation.name}' opened ({folder}).")

    def add_file_layer(self):
        from qgis.core import QgsRasterLayer, QgsVectorLayer
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add GIS data", "",
            "GIS data (*.gpkg *.shp *.geojson *.json *.kml *.kmz *.gpx *.csv *.tif *.tiff *.jp2 *.img *.vrt *.mbtiles);;"
            "All files (*.*)")
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
            self.say(f"Map image saved to {path} (georeferenced with a world file).")

    def export_data(self, preselect=None):
        dlg = ExportDialog(self.wb, preselect, self)
        if not dlg.exec():
            return
        token = self.busy.start("Exporting")
        QApplication.processEvents()
        try:
            files = dlg.run()
            self.say(f"Exported {len(files)} file(s) to {os.path.dirname(files[0])}.")
        except Exception as exc:
            self.say(f"Export failed: {exc}", "error")
        finally:
            self.busy.stop(token)

    def open_table(self, layer):
        if layer is None or layer.type() != QgsMapLayer.VectorLayer:
            return
        dlg = self._tables.get(layer.id())
        if dlg is None or not dlg.isVisible():
            dlg = AttributeTableDialog(layer, self.canvas, self)
            self._tables[layer.id()] = dlg
        dlg.show()
        dlg.raise_()

    def open_current_table(self):
        layer = self._selection[0][0] if self._selection else None
        if layer is None and "PICTURE" in self.panels.made:
            layer = self.panels["PICTURE"].tree.currentLayer()
        self.open_table(layer or self.wb.operation.layer("units"))

    def rename_layer(self, layer):
        text, ok = QInputDialog.getText(self, "Rename layer", "Name:", text=layer.name())
        if ok and text.strip():
            layer.setName(text.strip())

    def recolor_layer(self, layer):
        from qgis.PyQt.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(layer.customProperty("badr/color") or "#ffd24a"), self, "Layer colour")
        if c.isValid():
            exchange.style_user_layer(layer, c.name())

    def open_settings(self):
        if SettingsDialog(self).exec():
            self._apply_classification()
            self.agent.reset()
            self._update_ai_state()

    def open_algorithm(self, alg_id):
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
        self.btn_select.setChecked(False)
        self.tool_hint.setText(text)
        self.tool_hint.setVisible(True)
        self.canvas.setFocus()

    def pick(self, count, callback, text):
        self._arm(PickTool(self.canvas, self.wb, count), callback, text)

    def sketch(self, polygon, callback, text, color="#4a90e2"):
        self._arm(SketchTool(self.canvas, self.wb, polygon, color), callback, text)

    def start_measure(self):
        tool = MeasureTool(self.canvas, self.wb)
        tool.measured.connect(self._show_measure)
        self._arm(tool, lambda pts: None, "Measure: click points, right-click or Esc to finish")
        self.btn_measure.setChecked(True)

    def _show_measure(self, total, leg, brg):
        def fmt(m):
            return f"{m / 1000:.2f} km" if m >= 1000 else f"{m:.0f} m"
        self.info_label.setText(f"total {fmt(total)}   leg {fmt(leg)} @ {brg:03.0f}°")

    def cancel_tool(self):
        self.tool_hint.setVisible(False)
        if self.canvas.mapTool() is not self.select_tool:
            self.canvas.setMapTool(self.select_tool)
        self._tool = None
        self.btn_select.setChecked(True)
        self.btn_measure.setChecked(False)

    def _to_wgs(self, map_point):
        tr = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.wb.wgs84, self.wb.project)
        p = tr.transform(map_point)
        return p.y(), p.x()

    def _cursor_moved(self, map_point):
        self._cursor = map_point
        if not self._cursor_timer.isActive():
            self._cursor_timer.start()         # at most ~14 updates a second, however fast the mouse moves

    def _show_cursor(self):
        if self._cursor is None:
            return
        try:
            lat, lon = self._to_wgs(self._cursor)
        except Exception:
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return
        if Settings.get("ui/coord_format") == "MGRS":
            self.coord_label.setText(f"{to_mgrs(lat, lon, 5)}    {format_dd(lat, lon)}")
        else:
            self.coord_label.setText(f"{format_dd(lat, lon)}    {to_mgrs(lat, lon, 5)}")
        dem = self.wb.dem()
        if dem is not None:
            tr = QgsCoordinateTransform(self.wb.wgs84, dem.crs(), self.wb.project)
            val, ok = dem.dataProvider().sample(tr.transform(QgsPointXY(lon, lat)), 1)
            self.elev_label.setText(f"elev {val:.0f} m" if ok and val == val else "")
        else:
            self.elev_label.setText("")

    def _update_scale(self):
        self.scale_label.setText(f"1:{self.canvas.scale():,.0f}")

    def zoom_to_picture(self):
        op = self.wb.operation
        box = None
        for key in ("units", "incidents", "lines", "areas", "tracks"):
            layer = op.layer(key)
            if layer.featureCount():
                layer.updateExtents()
                e = layer.extent()
                box = QgsRectangle(e) if box is None else box
                box.combineExtentWith(e)
        if box is None or box.isEmpty() and box.width() == 0:
            self.say("Nothing on the map yet.", "warn")
            return
        if box.width() < 1e-5 and box.height() < 1e-5:
            self.wb.zoom_to(box.center().y(), box.center().x())
        else:
            self.wb.zoom_to_bbox(box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())

    # ---- selection ---------------------------------------------------------
    def _selectable_layers(self):
        """Vector layers in hit-test priority: points first, areas last."""
        op = self.wb.operation
        first = [op.layer(k) for k in SELECT_ORDER]
        visible = {n.layer() for n in self.wb.project.layerTreeRoot().findLayers() if n.isVisible() and n.layer()}
        others = [l for l in visible if l.type() == QgsMapLayer.VectorLayer and l not in first
                  and l is not op.layer("trails") and l is not op.layer("footprints")]
        others.sort(key=lambda l: l.geometryType())
        return [l for l in first if l in visible] + others

    def _hit(self, map_point):
        """Feature under a map point -> (layer, feature) or (None, None)."""
        tol = self.canvas.mapUnitsPerPixel() * 11
        crs = self.canvas.mapSettings().destinationCrs()
        best_poly = None
        for layer in self._selectable_layers():
            tr = QgsCoordinateTransform(crs, layer.crs(), self.wb.project)
            try:
                rect = tr.transformBoundingBox(QgsRectangle(map_point.x() - tol, map_point.y() - tol,
                                                            map_point.x() + tol, map_point.y() + tol))
                pt = tr.transform(map_point)
            except Exception:
                continue
            probe = QgsGeometry.fromRect(rect)
            centre = QgsGeometry.fromPointXY(pt)
            for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                g = f.geometry()
                if g.isEmpty():
                    continue
                if layer.geometryType() == 2:
                    # an area is hit on its outline, or inside when nothing smaller is there
                    edge = QgsGeometry.fromPolylineXY(g.asPolygon()[0]) if not g.isMultipart() and g.asPolygon() \
                        else g.convertToType(1, False)
                    if edge is not None and edge.intersects(probe):
                        return layer, f
                    if g.contains(centre) and (best_poly is None or g.area() < best_poly[2]):
                        best_poly = (layer, f, g.area())
                elif g.intersects(probe):
                    return layer, f
        if best_poly:
            return best_poly[0], best_poly[1]
        return None, None

    def _select_at(self, map_point, additive=False):
        layer, feat = self._hit(map_point)
        if layer is None:
            if not additive:
                self.clear_selection()
            return
        self.select_feature(layer, feat.id(), additive)

    def _select_box(self, rect, additive=False):
        if not additive:
            self.clear_selection()
        crs = self.canvas.mapSettings().destinationCrs()
        total = 0
        for layer in self._selectable_layers():
            tr = QgsCoordinateTransform(crs, layer.crs(), self.wb.project)
            try:
                r = tr.transformBoundingBox(rect)
            except Exception:
                continue
            ids = [f.id() for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(r))
                   if f.geometry().intersects(QgsGeometry.fromRect(r))]
            for fid in ids:
                if (layer, fid) not in self._selection:
                    self._selection.append((layer, fid))
            total += len(ids)
        self._apply_selection()
        self.info_label.setText(f"{len(self._selection)} selected")

    def select_feature(self, layer, fid, additive=False):
        if additive:
            if (layer, fid) in self._selection:
                self._selection.remove((layer, fid))
            else:
                self._selection.append((layer, fid))
        else:
            self._selection = [(layer, fid)]
        self._apply_selection()

    def clear_selection(self):
        self._selection = []
        self._apply_selection()

    def _apply_selection(self):
        for band in self._bands:
            self.canvas.scene().removeItem(band)
        self._bands = []
        by_layer = {}
        for layer, fid in self._selection:
            by_layer.setdefault(layer, []).append(fid)
        for lyr in self.wb.project.mapLayers().values():
            if lyr.type() == QgsMapLayer.VectorLayer and lyr.selectedFeatureCount() and lyr not in by_layer:
                lyr.removeSelection()
        accent = QColor(theme.ACCENT)
        for layer, fids in by_layer.items():
            layer.selectByIds(fids)
            for f in layer.getFeatures(QgsFeatureRequest().setFilterFids(fids)):
                g = f.geometry()
                if layer.geometryType() == 0:
                    band = QgsRubberBand(self.canvas, QgsWkbTypes.PointGeometry)
                    band.setIcon(QgsRubberBand.ICON_CIRCLE)
                    band.setIconSize(40)
                    band.setWidth(3)
                    band.setColor(accent)
                    band.setFillColor(QColor(74, 144, 226, 30))
                else:
                    band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry if layer.geometryType() == 1
                                         else QgsWkbTypes.PolygonGeometry)
                    band.setWidth(3)
                    band.setColor(accent)
                    band.setFillColor(QColor(74, 144, 226, 25))
                band.setToGeometry(g, layer)
                self._bands.append(band)
        if len(self._selection) == 1:
            layer, fid = self._selection[0]
            f = next(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
            if f is not None:
                self.inspector.show_feature(layer, f)
            else:
                self.inspector.clear()
        else:
            self.inspector.clear()
        self.info_label.setText(f"{len(self._selection)} selected" if len(self._selection) > 1 else "")

    def _selected_feature(self):
        if len(self._selection) != 1:
            return None, None
        layer, fid = self._selection[0]
        return layer, next(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)

    def _cop_key(self, layer):
        for key, lyr in self.wb.operation.layers.items():
            if lyr is layer:
                return key
        return None

    def zoom_to_selection(self):
        layer, f = self._selected_feature()
        if f is None:
            return
        tr = QgsCoordinateTransform(layer.crs(), self.wb.wgs84, self.wb.project)
        box = tr.transformBoundingBox(f.geometry().boundingBox())
        c = box.center()
        radius = max(600.0, geodesy.distance(box.yMinimum(), box.xMinimum(), box.yMaximum(), box.xMaximum()) * 0.9)
        self.fly_to(c.y(), c.x(), radius)

    def delete_selected(self):
        if not self._selection:
            return
        op = self.wb.operation
        n = 0
        for layer, fid in list(self._selection):
            key = self._cop_key(layer)
            if key in ("tracks", "trails", "footprints"):
                continue
            f = next(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
            if f is None:
                continue
            if key and "uid" in layer.fields().names():
                op.remove(key, f["uid"])
            else:
                layer.dataProvider().deleteFeatures([fid])
                layer.triggerRepaint()
                op.touch()
            n += 1
        self.clear_selection()
        if n:
            self.say(f"Deleted {n} feature(s).")

    def _inspector_edited(self):
        layer, f = self._selected_feature()
        key = self._cop_key(layer) if layer is not None else None
        if key:
            self.wb.operation.changed.emit(key)
        else:
            self.wb.operation.touch()
        if f is not None:
            self.inspector.show_feature(layer, f)

    def _review_selected(self, accept):
        layer, f = self._selected_feature()
        key = self._cop_key(layer) if layer is not None else None
        if f is None or not key:
            return
        self._do_resolve(key, f["uid"], accept)

    # ---- right-click ---------------------------------------------------------
    def build_context_menu(self, map_point):
        lat, lon = self._to_wgs(map_point)
        here = [lat, lon]
        grid = to_mgrs(lat, lon, 4)
        m = QMenu(self)
        layer, feat = self._hit(map_point)
        if feat is not None:
            self.select_feature(layer, feat.id())
            names = layer.fields().names()
            key = self._cop_key(layer)
            label = ""
            if "name" in names and feat["name"]:
                label = str(feat["name"])
            elif "sidc" in names and feat["sidc"]:
                label = S.describe(str(feat["sidc"]))
            else:
                label = layer.name()
            head = m.addAction(label[:40])
            head.setEnabled(False)
            m.addAction("Attributes", lambda: self.inspector.setVisible(True))
            m.addAction("Zoom to", self.zoom_to_selection)
            if key and "sidc" in names:
                sub = m.addMenu("Set affiliation")
                uid = feat["uid"]
                for a in ("friend", "hostile", "neutral", "unknown", "suspect"):
                    sub.addAction(a.capitalize(), lambda a=a, uid=uid: (
                        self.do("update_feature", uid=uid, affiliation=a, quiet=True), self._reselect()))
            if key and "review" in names and feat["review"] == "pending":
                uid = feat["uid"]
                m.addAction("Accept", lambda: self._do_resolve(key, uid, True))
                m.addAction("Reject", lambda: self._do_resolve(key, uid, False))
            m.addAction("Open layer table", lambda: self.open_table(layer))
            m.addAction("Delete", self.delete_selected)
            m.addSeparator()
        m.addAction(f"Copy {grid}", lambda: QApplication.clipboard().setText(grid))
        m.addAction("Viewshed from here", lambda: self.do("terrain", analysis="viewshed", location=here))
        m.addAction("Range rings 1 / 3 / 5 km", lambda: self.do("terrain", analysis="range_rings", location=here,
                                                               radii_m=[1000, 3000, 5000]))
        m.addAction("Cordon here", lambda: self.do("terrain", analysis="cordon", location=here))
        m.addAction("Ask about this location", lambda: self._ask_about(grid))
        return m

    def _context_menu(self, map_point, global_pos):
        self.build_context_menu(map_point).exec(global_pos)

    def _reselect(self):
        sel = list(self._selection)
        self._selection = sel
        self._apply_selection()

    def _ask_about(self, grid):
        self.command.setText(f"At {grid}: ")
        self.command.setFocus()

    # ---- camera ------------------------------------------------------------
    def fly_to(self, lat, lon, radius_m=1500.0, done=None):
        """Animated pan + zoom (ease in/out) so the eye can follow the move."""
        ext = self.wb.extent_wgs84()
        if self._fly is not None:
            self._fly.stop()
            self._fly = None
        if not ext or not self.isVisible():
            self.wb.zoom_to(lat, lon, radius_m)
            if done:
                done()
            return
        c0 = ((ext[1] + ext[3]) / 2, (ext[0] + ext[2]) / 2)
        r0 = max(50.0, geodesy.distance(c0[0], ext[0], c0[0], ext[2]) / 2)
        frames, state = 16, {"i": 0}
        timer = QTimer(self)
        timer.setInterval(40)

        def step():
            state["i"] += 1
            t = state["i"] / frames
            e = t * t * (3 - 2 * t)                       # smoothstep
            la = c0[0] + (lat - c0[0]) * e
            lo = c0[1] + (lon - c0[1]) * e
            r = math.exp(math.log(r0) + (math.log(max(radius_m, 50.0)) - math.log(r0)) * e)
            self.wb.zoom_to(la, lo, r)
            if state["i"] >= frames:
                timer.stop()
                self._fly = None
                if done:
                    done()
        timer.timeout.connect(step)
        self._fly = timer
        timer.start()

    def _story_focus(self, beat):
        def flash():
            geom = QgsGeometry.fromPointXY(QgsPointXY(beat["lon"], beat["lat"]))
            uid = beat.get("uid")
            if uid:
                key, f = self.wb.operation.find_any(uid)
                if f is not None:
                    geom = f.geometry()
                    self.select_feature(self.wb.operation.layer(key), f.id())
            try:
                self.canvas.flashGeometries([geom], self.wb.wgs84, QColor(255, 255, 255, 0),
                                            QColor(theme.ACCENT), 3, 420)
            except Exception:
                pass
        self.fly_to(beat["lat"], beat["lon"], beat.get("zoom_m", 1500.0), flash)

    # ---- running work --------------------------------------------------------
    def run_async(self, label, work, done):
        """Run ``work()`` off the GUI thread with the activity indicator animating;
        ``done(result)`` runs back on the GUI thread."""
        token = self.busy.start(label)

        def finish(ok, value):
            self.busy.stop(token)
            if not ok:
                self.say(str(value), "error")
                return
            try:
                done(value)
            except Exception as exc:
                self.say(f"{type(exc).__name__}: {exc}", "error")
            self._sync_layers()

        def runner():
            try:
                result = (True, work())
            except Exception as exc:
                result = (False, exc)
            self._invoke.emit(lambda: finish(*result))
        threading.Thread(target=runner, daemon=True).start()

    def _do_async(self, tool, args):
        """Network-bound tools: download in a worker thread, load on the GUI thread."""
        wb = self.wb
        if tool == "get_elevation" or (tool == "terrain" and args.get("analysis") == "load_elevation"):
            from ..analysis import dem
            bbox, out = wb.extent_wgs84(), wb.out_path("dem")

            def loaded(source):
                layer = dem.register_dem(wb, out)
                self.say(f"Elevation loaded from {source} ({layer.width()}x{layer.height()} px).")
            self.run_async("Downloading elevation", lambda: dem.fetch_elevation(bbox, out), loaded)
            return True
        if tool in ("reference_data", "reference") and args.get("action") == "osm":
            from ..analysis import osm
            theme_name, bbox = str(args.get("name", "")).lower(), wb.extent_wgs84()
            if theme_name not in osm.THEMES:
                return False

            def loaded(payload):
                res = osm.load_theme(wb, theme_name, payload)
                self.say(res["summary"])
            self.run_async(f"Importing OSM {theme_name}", lambda: osm.fetch_checked(theme_name, bbox), loaded)
            return True
        if tool == "imagery" and args.get("action") in ("add", "change"):
            from ..gee import client as gee
            if not gee.status()[0] and not Settings.get("gee/project"):
                return False                     # let the normal path produce the explanatory error
            bbox = wb.extent_wgs84()

            def prepare():
                if not gee.available():
                    raise RuntimeError(gee.status()[1])
                if args["action"] == "add":
                    return gee.prepare_layer(args["product"], bbox, args.get("start"), args.get("end"))
                return gee.prepare_change((args["before_start"], args["before_end"]),
                                          (args["after_start"], args["after_end"]), args.get("method", "optical"), bbox)

            def loaded(prepared):
                res = gee.finish_layer(wb, *prepared)
                self.say(res["summary"])
            self.run_async("Contacting Earth Engine", prepare, loaded)
            return True
        return False

    def do(self, tool, quiet=False, **args):
        """Manual actions call the same Toolbox the assistant uses."""
        args = {k: v for k, v in args.items() if v not in (None, "")}
        try:
            if self._do_async(tool, args):
                return None
        except Exception as exc:
            self.say(str(exc), "error")
            return None
        label = {"terrain": f"Computing {str(args.get('analysis', 'terrain')).replace('_', ' ')}",
                 "incident_analysis": "Analysing incidents", "run_gis_tool": "Running tool"}.get(tool, "Working")
        token = self.busy.start(label)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        QApplication.processEvents()            # let the indicator paint before the computation starts
        try:
            text, err = self.manual.call(tool, args)
        finally:
            QApplication.restoreOverrideCursor()
            self.busy.stop(token)
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
        self.feed.append(f"<div style='margin:2px 0'>{fragment}</div>")
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

    def _update_ai_state(self):
        if Settings.ai_ready():
            self.ai_state.setText(f"<span style='color:{theme.OK}'>&#9679;</span> {html.escape(Settings.model())}")
        else:
            self.ai_state.setText(f"<span style='color:{theme.WARN}'>&#9679;</span> assistant not set up")

    def _submit(self):
        text = self.command.text().strip()
        if not text:
            return
        self.command.clear()
        self._history.append(text)
        self._hist_i = len(self._history)
        self.ask(text)

    def _echo(self, text, tag=""):
        extra = f" <span style='color:{theme.MUTED}'>{tag}</span>" if tag else ""
        self.feed_html(f"<div style='color:{theme.ACCENT};margin-top:6px'>&rsaquo; {html.escape(text)}{extra}</div>")

    def ask(self, text):
        """Route an input: local first (free and instant), the model only when needed."""
        if text.lower() in ("/help", "help", "?"):
            self.feed_html(HELP)
            return
        reply = small_talk(text)
        if reply:
            self._echo(text)
            self.say(reply)
            return
        quick = route_quick(text)
        if quick:
            self._echo(text, "direct")
            out = self.do(quick[0], **quick[1])
            if isinstance(out, str):
                self.say(out)
            return
        if self._busy:
            self.say("Still working on the previous request (press Stop to cancel).", "warn")
            return
        self._echo(text)
        if not Settings.ai_ready():
            self.say("The assistant needs a model: open Settings and add an API key, or choose a local model. "
                     "Everything in the left panels works without it.", "warn")
            return
        self._busy = True
        self.stop_btn.setVisible(True)
        self.send_btn.setVisible(False)
        self._ai_token = self.ai_busy.start("Thinking")
        image = self._snapshot() if re.search(r"\b(look at|see on|screenshot|this image|what is this)\b", text, re.I) else None
        threading.Thread(target=self.agent.run, args=(text, image), daemon=True).start()

    def _snapshot(self):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        self.canvas.grab().scaledToWidth(900, Qt.TransformationMode.SmoothTransformation).save(buf, "PNG")
        return base64.b64encode(bytes(ba)).decode("ascii")

    def _stop(self):
        self.agent.abort()
        self._set_ai_status("Stopping")

    def _set_ai_status(self, text):
        if self._ai_token is not None:
            self._ai_token = self.ai_busy.update_text(self._ai_token, text)

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

    TOOL_LABELS = {"get_picture": "Reading the map", "place_symbol": "Placing symbol", "terrain": "Terrain analysis",
                   "incidents": "Analysing incidents", "imagery": "Imagery", "draw_graphic": "Drawing",
                   "log_incident": "Logging incident", "qgis": "QGIS tool", "export": "Exporting",
                   "go_to": "Moving the map", "sketch": "Sketching", "sensors": "Sensors"}

    def _on_agent_event(self, kind, data):
        if kind == "status":
            self._set_ai_status(str(data))
        elif kind == "note":
            self.feed_html(f"<span style='color:{theme.MUTED}'>{self._md(data)}</span>")
        elif kind == "tool":
            self._set_ai_status(self.TOOL_LABELS.get(data["name"], data["name"]))
            args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in data["args"].items())
            self.feed_html(f"<span style='color:{theme.MUTED};font-size:8pt'>&#9656; {html.escape(data['name'])} "
                           f"{html.escape(args[:110])}</span>")
        elif kind == "tool_result":
            self._sync_layers()
            if not data["ok"]:
                self.feed_html(f"<span style='color:{theme.WARN};font-size:8pt'>&nbsp;&nbsp;"
                               f"{html.escape(data['text'][:160])}</span>")
        elif kind == "usage":
            cached = f" + {data['cached']:,} cached" if data["cached"] else ""
            self._usage_note = f"{data['calls']} call(s) &middot; {data['in']:,} in{cached} &middot; {data['out']:,} out"
        elif kind == "story":
            self.story.set_story(data)
        elif kind in ("final", "error"):
            if kind == "final" and data:
                # plain-text answer (models that ignore the brief tool): still show it as beats
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(data)) if s.strip()]
                self.story.set_story([{"say": s} for s in sentences[:6]] or [{"say": str(data)}])
            elif kind == "error":
                self.say(data, "error")
            note = getattr(self, "_usage_note", "")
            if note and kind == "final":
                self.feed_html(f"<span style='color:{theme.MUTED};font-size:8pt'>{note} tokens</span>")
            self._usage_note = ""
            self._busy = False
            self.stop_btn.setVisible(False)
            self.send_btn.setVisible(True)
            self.ai_busy.stop(self._ai_token)
            self._ai_token = None
            self._sync_layers()

    def _clear_chat(self):
        self.feed.clear()
        self.story.set_story([])
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
            it = QListWidgetItem(f"{label}  -  {what}".strip(" -") if label else str(what))
            it.setData(Qt.ItemDataRole.UserRole, [key, str(f["uid"])])
            if "sidc" in names and f["sidc"]:
                it.setIcon(QIcon(SymbolEngine.instance().pixmap(f["sidc"], 22)))
            self.review_list.addItem(it)
        n = len(pending)
        self.review_head.setText(f"Review queue  ({n} pending)")
        self.review_head.setStyleSheet(f"color:{theme.WARN};")
        self.review_box.setVisible(n > 0)
        if n:
            self.review_list.setCurrentRow(0)

    def _review_clicked(self, it):
        key, uid = it.data(Qt.ItemDataRole.UserRole)
        f = self.wb.operation.find(key, uid)
        if f is not None:
            p = f.geometry().centroid().asPoint()
            self.select_feature(self.wb.operation.layer(key), f.id())
            self.fly_to(p.y(), p.x(), 1500)

    def _do_resolve(self, key, uid, accept):
        f = self.wb.operation.find(key, uid)
        label = ""
        if f is not None:
            names = self.wb.operation.layer(key).fields().names()
            label = str((f["name"] if "name" in names else f["label"]) or "item")
        ok = self.wb.operation.resolve(key, uid, accept)
        if not ok:
            self.say("That item is no longer in the picture.", "warn")
        else:
            self.say(f"{'Accepted' if accept else 'Rejected'}: {label}.")
        self.clear_selection()
        self._refresh_review()

    def _resolve(self, accept):
        it = self.review_list.currentItem() or self.review_list.item(0)
        if it is None:
            return
        key, uid = it.data(Qt.ItemDataRole.UserRole)
        self._do_resolve(key, uid, accept)

    def _accept_all(self):
        pending = self.wb.operation.pending()
        for key, f in pending:
            self.wb.operation.resolve(key, f["uid"], True)
        self.say(f"Accepted {len(pending)} item(s).")
        self._refresh_review()

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
        self.clock.setText(datetime.now(timezone.utc).strftime("%d%H%MZ %b %y").upper())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.map_toolbar.raise_()

    def closeEvent(self, e):
        if self._sim:
            self._sim.stop()
        self.devices.shutdown()
        self._clock.stop()
        for sig, slot in ((SymbolEngine.instance().rendered, self._symbols_ready),):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            self.wb.operation.close()          # write any unsaved edits
        except Exception as exc:
            print(f"[BADR] could not save the operation: {exc}")
        super().closeEvent(e)
