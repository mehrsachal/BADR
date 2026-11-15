"""Map interaction: pick N points, or sketch a line / polygon."""
from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


class PickTool(QgsMapTool):
    """Collect ``count`` clicks (WGS84 lat/lon) then call ``done([(lat, lon), ...])``."""

    finished = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, canvas, workbench, count=1, color="#ffb000"):
        super().__init__(canvas)
        self.wb = workbench
        self.count = count
        self.points = []
        self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor(color))
        self.band.setWidth(2)
        self.band.setLineStyle(Qt.PenStyle.DashLine)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _wgs(self, map_point):
        tr = QgsCoordinateTransform(self.canvas().mapSettings().destinationCrs(), self.wb.wgs84, self.wb.project)
        p = tr.transform(map_point)
        return p.y(), p.x()

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._reset()
            self.cancelled.emit()
            return
        mp = self.toMapCoordinates(e.pos())
        self.points.append(self._wgs(mp))
        self.band.addPoint(mp)
        if len(self.points) >= self.count:
            pts = list(self.points)
            self._reset()
            self.finished.emit(pts)

    def canvasMoveEvent(self, e):
        if self.points and self.count > 1:
            self.band.movePoint(self.toMapCoordinates(e.pos()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._reset()
            self.cancelled.emit()

    def _reset(self):
        self.points = []
        self.band.reset(QgsWkbTypes.LineGeometry)

    def deactivate(self):
        self._reset()
        super().deactivate()


class SketchTool(QgsMapTool):
    """Left-click adds vertices; right-click / double-click / Enter finishes."""

    finished = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, canvas, workbench, polygon=False, color="#00a8dc"):
        super().__init__(canvas)
        self.wb = workbench
        self.polygon = polygon
        self.points = []
        self.band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry if polygon else QgsWkbTypes.LineGeometry)
        c = QColor(color)
        self.band.setColor(c)
        self.band.setWidth(2)
        if polygon:
            fill = QColor(c)
            fill.setAlpha(40)
            self.band.setFillColor(fill)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _wgs(self, mp):
        tr = QgsCoordinateTransform(self.canvas().mapSettings().destinationCrs(), self.wb.wgs84, self.wb.project)
        p = tr.transform(mp)
        return p.y(), p.x()

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._finish()
            return
        mp = self.toMapCoordinates(e.pos())
        self.points.append(self._wgs(mp))
        self.band.addPoint(mp)

    def canvasDoubleClickEvent(self, e):
        self._finish()

    def canvasMoveEvent(self, e):
        if self.points:
            self.band.movePoint(self.toMapCoordinates(e.pos()))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
        elif e.key() == Qt.Key.Key_Escape:
            self._reset()
            self.cancelled.emit()
        elif e.key() == Qt.Key.Key_Backspace and self.points:
            self.points.pop()
            self.band.removeLastPoint()

    def _finish(self):
        need = 3 if self.polygon else 2
        pts = list(self.points)
        self._reset()
        if len(pts) >= need:
            self.finished.emit(pts)
        else:
            self.cancelled.emit()

    def _reset(self):
        self.points = []
        self.band.reset(QgsWkbTypes.PolygonGeometry if self.polygon else QgsWkbTypes.LineGeometry)

    def deactivate(self):
        self._reset()
        super().deactivate()


def to_points(latlons):
    return [QgsPointXY(lon, lat) for lat, lon in latlons]


class SelectTool(QgsMapTool):
    """Default map tool, ArcGIS-style 'explore':

    click            select the feature under the cursor (shows its attributes)
    Ctrl+click       add to / remove from the selection
    Shift+drag       select everything inside a box
    drag             pan
    wheel            zoom
    right-click      context menu for that spot
    """

    clicked = pyqtSignal(object, bool)       # QgsPointXY in map coordinates, additive
    boxed = pyqtSignal(object, bool)         # QgsRectangle in map coordinates, additive
    context = pyqtSignal(object, object)     # QgsPointXY map point, QPoint global position

    def __init__(self, canvas):
        super().__init__(canvas)
        self._press = None
        self._dragging = False
        self._box = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._press = e.pos()
        self._dragging = False
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._box = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
            self._box.setColor(QColor(74, 144, 226, 50))
            self._box.setStrokeColor(QColor(74, 144, 226))
            self._box.setWidth(1)

    def canvasMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._dragging and (e.pos() - self._press).manhattanLength() < 5:
            return
        self._dragging = True
        if self._box is not None:
            from qgis.core import QgsGeometry, QgsRectangle
            a, b = self.toMapCoordinates(self._press), self.toMapCoordinates(e.pos())
            self._box.setToGeometry(QgsGeometry.fromRect(QgsRectangle(a, b)), None)
        else:
            self.canvas().panAction(e)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.toMapCoordinates(e.pos()), self.canvas().mapToGlobal(e.pos()))
            return
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        additive = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self._box is not None:
            from qgis.core import QgsRectangle
            rect = QgsRectangle(self.toMapCoordinates(self._press), self.toMapCoordinates(e.pos()))
            self._box.reset(QgsWkbTypes.PolygonGeometry)
            self.canvas().scene().removeItem(self._box)
            self._box = None
            if self._dragging:
                self.boxed.emit(rect, additive)
        elif self._dragging:
            self.canvas().panActionEnd(e.pos())
        else:
            self.clicked.emit(self.toMapCoordinates(e.pos()), additive)
        self._press = None
        self._dragging = False


class MeasureTool(QgsMapTool):
    """Click to add points; shows leg and total distance. Right-click or Esc ends."""

    measured = pyqtSignal(float, float, float)     # total m, last leg m, last leg bearing
    finished = pyqtSignal(list)
    cancelled = pyqtSignal()

    def __init__(self, canvas, workbench):
        super().__init__(canvas)
        self.wb = workbench
        self.points = []
        self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor("#e5a23a"))
        self.band.setWidth(2)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _wgs(self, mp):
        tr = QgsCoordinateTransform(self.canvas().mapSettings().destinationCrs(), self.wb.wgs84, self.wb.project)
        p = tr.transform(mp)
        return p.y(), p.x()

    def _report(self, extra=None):
        from ..core import geodesy
        pts = self.points + ([extra] if extra else [])
        if len(pts) < 2:
            return
        legs = [geodesy.distance(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]
        brg = geodesy.bearing(pts[-2][0], pts[-2][1], pts[-1][0], pts[-1][1])
        self.measured.emit(sum(legs), legs[-1], brg)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._end()
            return
        mp = self.toMapCoordinates(e.pos())
        self.points.append(self._wgs(mp))
        self.band.addPoint(mp)
        self._report()

    def canvasMoveEvent(self, e):
        if self.points:
            mp = self.toMapCoordinates(e.pos())
            self.band.movePoint(mp)
            self._report(self._wgs(mp))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._end()

    def _end(self):
        pts = list(self.points)
        self.points = []
        self.band.reset(QgsWkbTypes.LineGeometry)
        if len(pts) >= 2:
            self.finished.emit(pts)
        else:
            self.cancelled.emit()

    def deactivate(self):
        self.points = []
        self.band.reset(QgsWkbTypes.LineGeometry)
        super().deactivate()
