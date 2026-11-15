"""Reusable interface pieces: splash, busy indicator, story player, inspector, dialogs."""
import os
import sys

from qgis.core import QgsFeatureRequest, QgsMapLayer
from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..core import exchange
from ..symbology import sidc as S
from ..symbology.engine import SymbolEngine
from . import icons, theme


def tool_button(icon_name, tip, slot=None, checkable=False, size=26):
    b = QToolButton()
    b.setIcon(icons.icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


# ---------------------------------------------------------------------------
class Splash(QWidget):
    """Start-up window with a real progress bar (each step is a start-up stage)."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(440, 170)
        self.setStyleSheet(f"background:{theme.PANEL}; color:{theme.TEXT};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 22)
        title = QLabel("BADR")
        title.setStyleSheet("font-size:22pt; font-weight:700; letter-spacing:2px;")
        sub = QLabel("Planning and analysis")
        sub.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        self.bar = QProgressBar()
        self.bar.setObjectName("splashbar")
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setStyleSheet(f"QProgressBar {{ background:#2a2b2f; border:none; max-height:4px; min-height:4px; }}"
                               f"QProgressBar::chunk {{ background:{theme.ACCENT}; }}")
        self.label = QLabel("Starting")
        self.label.setStyleSheet(f"color:{theme.MUTED}; font-size:8pt;")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addStretch(1)
        lay.addWidget(self.bar)
        lay.addWidget(self.label)
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def step(self, text, percent):
        self.label.setText(text)
        self.bar.setValue(int(percent))
        QApplication.processEvents()


def release_native_splash():
    """Tell the Windows launcher's instant splash to close (it waits on a named event)."""
    name = os.environ.get("BADR_SPLASH_EVENT")
    if not name or not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenEventW(0x0002, False, name)        # EVENT_MODIFY_STATE
        if handle:
            k32.SetEvent(handle)
            k32.CloseHandle(handle)
    except Exception:
        pass


# ---------------------------------------------------------------------------
class BusyIndicator(QWidget):
    """Animated activity strip + label.  Reference-counted: several tasks may overlap."""

    def __init__(self):
        super().__init__()
        self.setObjectName("bare")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.label = QLabel("")
        self.label.setObjectName("state")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)                  # indeterminate: Qt animates it
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(120)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        self._tasks = []
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def start(self, text):
        self._tasks.append(text)
        self._show()
        return text

    def update_text(self, old, new):
        if old in self._tasks:
            self._tasks[self._tasks.index(old)] = new
        self._show()
        return new

    def stop(self, text=None):
        if text in self._tasks:
            self._tasks.remove(text)
        elif self._tasks and text is None:
            self._tasks.pop()
        self._show()

    @property
    def active(self):
        return bool(self._tasks)

    def _show(self):
        on = bool(self._tasks)
        self.setVisible(on)
        if on:
            self.label.setText(self._tasks[-1])
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        if self._tasks:
            self._dots = (self._dots + 1) % 4
            self.label.setText(self._tasks[-1] + "." * self._dots)


# ---------------------------------------------------------------------------
class StoryPlayer(QFrame):
    """The assistant's answer as a few short beats that can be played on the map."""

    focus = pyqtSignal(dict)        # a beat with lat/lon to fly to

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = QLabel("Answer")
        self.title.setObjectName("h2")
        self.title.setStyleSheet("padding-top:0;")
        self.play_btn = tool_button("play", "Play: fly the map through the answer (Space)", self.toggle)
        self.prev_btn = tool_button("prev", "Previous", lambda: self.go(self.index - 1))
        self.next_btn = tool_button("next", "Next", lambda: self.go(self.index + 1))
        top.addWidget(self.title, 1)
        top.addWidget(self.prev_btn)
        top.addWidget(self.play_btn)
        top.addWidget(self.next_btn)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet(f"QListWidget {{ border:none; background:{theme.BG}; font-size:9.5pt; }}"
                                f"QListWidget::item {{ padding:5px 4px; border-left:2px solid transparent; }}"
                                f"QListWidget::item:selected {{ background:{theme.RAISED}; "
                                f"border-left:2px solid {theme.ACCENT}; }}")
        self.list.itemClicked.connect(lambda _it: self.go(self.list.currentRow(), stop=True))
        lay.addWidget(self.list)
        self.beats = []
        self.index = -1
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def set_story(self, beats):
        self.stop()
        self.beats = list(beats)
        self.list.clear()
        for b in self.beats:
            it = QListWidgetItem(b["say"])
            if "lat" not in b:
                it.setForeground(QColor(theme.MUTED))
            self.list.addItem(it)
        rows = sum(max(1, len(b["say"]) // 46 + 1) for b in self.beats)
        self.list.setFixedHeight(min(200, 12 + rows * 17 + len(self.beats) * 11))
        has_places = any("lat" in b for b in self.beats)
        for w in (self.play_btn, self.prev_btn, self.next_btn):
            w.setVisible(has_places)
        self.index = -1
        self.setVisible(bool(self.beats))

    @property
    def playing(self):
        return self._timer.isActive()

    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        if not self.beats:
            return
        self.play_btn.setIcon(icons.icon("pause"))
        start = 0 if self.index >= len(self.beats) - 1 else self.index + 1
        self.go(start)
        self._schedule()

    def stop(self):
        self._timer.stop()
        self.play_btn.setIcon(icons.icon("play"))

    def go(self, i, stop=False):
        if stop:
            self.stop()
        if not 0 <= i < len(self.beats):
            return
        self.index = i
        self.list.setCurrentRow(i)
        if "lat" in self.beats[i]:
            self.focus.emit(self.beats[i])

    def _schedule(self):
        words = len(self.beats[self.index]["say"].split()) if 0 <= self.index < len(self.beats) else 8
        self._timer.start(1700 + 190 * words)         # long enough to read the sentence and see the map settle

    def _advance(self):
        if self.index + 1 >= len(self.beats):
            self.stop()
            return
        self.go(self.index + 1)
        self._schedule()


# ---------------------------------------------------------------------------
READ_ONLY = {"uid", "sidc", "source", "review", "confidence", "device", "stale", "fid"}


class Inspector(QFrame):
    """Attributes of the selected map feature; values are edited in place."""

    changed = pyqtSignal()
    request_zoom = pyqtSignal()
    request_delete = pyqtSignal()
    request_review = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.icon = QLabel()
        self.icon.setFixedSize(30, 30)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight:600;")
        top.addWidget(self.icon)
        top.addWidget(self.title, 1)
        top.addWidget(tool_button("extent", "Zoom to this feature", self.request_zoom.emit))
        top.addWidget(tool_button("trash", "Delete this feature (Del)", self.request_delete.emit))
        lay.addLayout(top)
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.cellChanged.connect(self._edited)
        lay.addWidget(self.table)
        self.review_row = QWidget()
        self.review_row.setObjectName("bare")
        rl = QHBoxLayout(self.review_row)
        rl.setContentsMargins(0, 0, 0, 0)
        accept = QPushButton("Accept")
        accept.setObjectName("primary")
        accept.clicked.connect(lambda: self.request_review.emit(True))
        reject = QPushButton("Reject")
        reject.clicked.connect(lambda: self.request_review.emit(False))
        note = QLabel("Added by the assistant - awaiting your review")
        note.setObjectName("hint")
        rl.addWidget(note, 1)
        rl.addWidget(accept)
        rl.addWidget(reject)
        lay.addWidget(self.review_row)
        self.layer = None
        self.fid = None
        self._loading = False
        self.setVisible(False)

    def clear(self):
        self.layer, self.fid = None, None
        self.setVisible(False)

    def show_feature(self, layer, feature):
        self._loading = True
        self.layer, self.fid = layer, feature.id()
        names = layer.fields().names()
        sidc = feature["sidc"] if "sidc" in names else None
        if sidc and S.is_valid(str(sidc)):
            self.icon.setPixmap(SymbolEngine.instance().pixmap(str(sidc), 30))
            what = S.describe(str(sidc))
        else:
            self.icon.clear()
            what = (str(feature["kind"]) if "kind" in names and feature["kind"] else layer.name())
        label = ""
        for fld in ("name", "label"):
            if fld in names and feature[fld]:
                label = str(feature[fld])
                break
        self.title.setText(f"{label}\n{what}" if label else what)
        rows = [n for n in names if n not in ("fid",)]
        self.table.setRowCount(len(rows))
        for r, n in enumerate(rows):
            key = QTableWidgetItem(n)
            key.setFlags(Qt.ItemFlag.ItemIsEnabled)
            key.setForeground(QColor(theme.MUTED))
            v = feature[n]
            text = "" if v is None or str(v) == "NULL" else str(v)
            val = QTableWidgetItem(text)
            if n in READ_ONLY:
                val.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                val.setForeground(QColor(theme.MUTED))
            val.setData(Qt.ItemDataRole.UserRole, n)
            self.table.setItem(r, 0, key)
            self.table.setItem(r, 1, val)
        self.table.setFixedHeight(min(138, 4 + 22 * len(rows)))
        self.review_row.setVisible("review" in names and feature["review"] == "pending")
        self.setVisible(True)
        self._loading = False

    def _edited(self, row, col):
        if self._loading or col != 1 or self.layer is None:
            return
        item = self.table.item(row, 1)
        field = item.data(Qt.ItemDataRole.UserRole)
        idx = self.layer.fields().indexOf(field)
        if idx < 0:
            return
        text = item.text()
        value = text
        ftype = self.layer.fields().at(idx).typeName().lower()
        if any(t in ftype for t in ("int", "real", "double")):
            try:
                value = float(text) if "int" not in ftype else int(float(text))
            except ValueError:
                value = None
        self.layer.dataProvider().changeAttributeValues({self.fid: {idx: value}})
        self.layer.triggerRepaint()
        self.changed.emit()


# ---------------------------------------------------------------------------
class AttributeTableDialog(QDialog):
    """QGIS' own attribute table (sort, filter, select, edit) for any vector layer."""

    def __init__(self, layer, canvas, parent=None):
        super().__init__(parent)
        from qgis.gui import QgsDualView
        self.layer = layer
        self.setWindowTitle(f"Attributes - {layer.name()} ({layer.featureCount()})")
        self.resize(820, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QHBoxLayout()
        self.edit = QPushButton("Edit values")
        self.edit.setCheckable(True)
        self.edit.toggled.connect(self._toggle_edit)
        zoom = QPushButton("Zoom to selected")
        zoom.clicked.connect(lambda: canvas.zoomToSelected(layer) if canvas else None)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete)
        clear = QPushButton("Clear selection")
        clear.clicked.connect(layer.removeSelection)
        self.count = QLabel("")
        self.count.setObjectName("hint")
        for w in (self.edit, zoom, delete, clear):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.count)
        lay.addLayout(bar)
        self.view = QgsDualView()
        self.view.init(layer, canvas, QgsFeatureRequest())
        self.view.setView(QgsDualView.AttributeTable)
        lay.addWidget(self.view, 1)
        layer.selectionChanged.connect(self._count)
        self._count()

    def _count(self, *_):
        self.count.setText(f"{self.layer.selectedFeatureCount()} selected of {self.layer.featureCount()}")

    def _toggle_edit(self, on):
        if on:
            self.layer.startEditing()
        else:
            self.layer.commitChanges()
            self.layer.triggerRepaint()

    def _delete(self):
        ids = self.layer.selectedFeatureIds()
        if not ids:
            return
        if self.layer.isEditable():
            self.layer.deleteSelectedFeatures()
        else:
            self.layer.dataProvider().deleteFeatures(ids)
            self.layer.removeSelection()
            self.layer.triggerRepaint()
            self.layer.dataChanged.emit()

    def closeEvent(self, e):
        if self.layer.isEditable():
            self.layer.commitChanges()
        super().closeEvent(e)


class ExportDialog(QDialog):
    def __init__(self, wb, preselect=None, parent=None):
        super().__init__(parent)
        self.wb = wb
        self.setWindowTitle("Export data")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Layers to export"))
        self.layers = QListWidget()
        cop = set(wb.operation.layers.values()) if wb.operation else set()
        for lyr in exchange.exportable_layers(wb.project):
            kind = "raster" if lyr.type() == QgsMapLayer.RasterLayer else f"{lyr.featureCount()} features"
            it = QListWidgetItem(f"{lyr.name()}   ({kind})")
            it.setData(Qt.ItemDataRole.UserRole, lyr.id())
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = (lyr.id() in preselect) if preselect else (lyr in cop or bool(lyr.customProperty("badr/user")))
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.layers.addItem(it)
        self.layers.setMinimumHeight(190)
        lay.addWidget(self.layers)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(list(exchange.FORMATS))
        self.selected_only = QCheckBox("Only selected features (where a layer has a selection)")
        base = wb.operation.folder if wb.operation and wb.operation.folder else os.path.join(
            os.path.expanduser("~"), "BADR")
        self.folder = QLineEdit(os.path.join(base, "export"))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow("Format", self.format)
        form.addRow("Folder", row)
        form.addRow("", self.selected_only)
        lay.addLayout(form)
        note = QLabel("Vector layers are written in WGS84. NATO symbols export with their SIDC code, so ArcGIS, "
                      "QGIS or TAK can re-symbolise them. Rasters are copied as GeoTIFF.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox()
        bb.addButton("Export", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Export folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def chosen(self):
        out = []
        for i in range(self.layers.count()):
            it = self.layers.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                lyr = self.wb.project.mapLayer(it.data(Qt.ItemDataRole.UserRole))
                if lyr:
                    out.append(lyr)
        return out

    def run(self):
        """-> list of files written (raises on failure)."""
        return exchange.export_layers(self.chosen(), self.folder.text().strip(), self.format.currentText(),
                                      self.wb.operation.name if self.wb.operation else "badr_export",
                                      self.selected_only.isChecked())


class ColorButton(QPushButton):
    def __init__(self, color="#ffd24a"):
        super().__init__()
        self.setFixedWidth(46)
        self.color = color
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self):
        self.setStyleSheet(f"background:{self.color}; border:1px solid {theme.LINE};")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Layer colour")
        if c.isValid():
            self.color = c.name()
            self._paint()


class NewLayerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New layer")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Key terrain, Notes, Water points")
        self.geometry = QComboBox()
        for label, key in (("Points", "point"), ("Lines", "line"), ("Areas (polygons)", "polygon")):
            self.geometry.addItem(label, key)
        self.color = ColorButton()
        self.fields = QLineEdit()
        self.fields.setPlaceholderText("optional extra columns, comma separated: type, owner, grid")
        form.addRow("Name", self.name)
        form.addRow("Type", self.geometry)
        form.addRow("Colour", self.color)
        form.addRow("Columns", self.fields)
        lay.addLayout(form)
        hint = QLabel("Every layer gets 'name' and 'notes' columns. The layer is saved with the operation.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        extra = [f.strip() for f in self.fields.text().split(",") if f.strip()]
        return self.name.text().strip(), self.geometry.currentData(), self.color.color, extra
