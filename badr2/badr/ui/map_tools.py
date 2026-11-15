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
