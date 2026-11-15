"""Line icons drawn in code: one stroke weight, one grid, no icon font or image files."""
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

GRID = 20.0


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _picture(p):
    for dy in (0, 4, 8):
        p.drawPolygon(_poly((10, 3 + dy), (17, 6.5 + dy), (10, 10 + dy), (3, 6.5 + dy))) if dy == 0 else \
            p.drawPolyline(_poly((3, 6.5 + dy), (10, 10 + dy), (17, 6.5 + dy)))


def _plan(p):
    p.drawRect(QRectF(3, 6, 14, 9))
    p.drawLine(QPointF(3, 6), QPointF(17, 15))
    p.drawLine(QPointF(3, 15), QPointF(17, 6))


def _terrain(p):
    p.drawPolyline(_poly((2, 16), (7.5, 6), (10.5, 11), (13, 7.5), (18, 16)))
    p.drawLine(QPointF(2, 16), QPointF(18, 16))


def _intel(p):
    p.drawEllipse(QPointF(10, 10), 6.5, 6.5)
    p.drawEllipse(QPointF(10, 10), 2.2, 2.2)
    for a, b in (((10, 1.5), (10, 4.5)), ((10, 15.5), (10, 18.5)), ((1.5, 10), (4.5, 10)), ((15.5, 10), (18.5, 10))):
        p.drawLine(QPointF(*a), QPointF(*b))


def _imagery(p):
    p.drawRect(QRectF(3, 3, 14, 14))
    p.drawLine(QPointF(3, 10), QPointF(17, 10))
    p.drawLine(QPointF(10, 3), QPointF(10, 17))
    p.drawPolyline(_poly((3, 14), (7, 11), (10, 13)))


def _sensors(p):
    p.drawEllipse(QPointF(10, 12), 1.6, 1.6)
    for r in (4.5, 8):
        p.drawArc(QRectF(10 - r, 12 - r, 2 * r, 2 * r), 35 * 16, 110 * 16)
    p.drawLine(QPointF(10, 13.6), QPointF(10, 18))


def _draw(p):
    p.drawPolygon(_poly((4, 16), (4.8, 12.5), (13.5, 3.8), (16.2, 6.5), (7.5, 15.2)))
    p.drawLine(QPointF(11.8, 5.5), QPointF(14.5, 8.2))


def _tools(p):
    for y, x in ((5, 7), (10, 13), (15, 9)):
        p.drawLine(QPointF(3, y), QPointF(17, y))
        p.drawEllipse(QPointF(x, y), 1.9, 1.9)


def _select(p):
    p.drawPolygon(_poly((5, 3), (5, 16), (8.6, 12.8), (11, 17.5), (13, 16.5), (10.7, 11.8), (15.5, 11.5)))


def _pan(p):
    p.drawLine(QPointF(10, 3), QPointF(10, 17))
    p.drawLine(QPointF(3, 10), QPointF(17, 10))
    for pts in (((7.5, 5.5), (10, 3), (12.5, 5.5)), ((7.5, 14.5), (10, 17), (12.5, 14.5)),
                ((5.5, 7.5), (3, 10), (5.5, 12.5)), ((14.5, 7.5), (17, 10), (14.5, 12.5))):
        p.drawPolyline(_poly(*pts))


def _zoom(p, plus):
    p.drawEllipse(QPointF(8.5, 8.5), 5.5, 5.5)
    p.drawLine(QPointF(12.6, 12.6), QPointF(17.5, 17.5))
    p.drawLine(QPointF(6, 8.5), QPointF(11, 8.5))
    if plus:
        p.drawLine(QPointF(8.5, 6), QPointF(8.5, 11))


def _extent(p):
    for pts in (((3, 7), (3, 3), (7, 3)), ((13, 3), (17, 3), (17, 7)), ((17, 13), (17, 17), (13, 17)),
                ((7, 17), (3, 17), (3, 13))):
        p.drawPolyline(_poly(*pts))
    p.drawRect(QRectF(7.5, 7.5, 5, 5))


def _measure(p):
    p.drawPolygon(_poly((2.5, 13.5), (13.5, 2.5), (17.5, 6.5), (6.5, 17.5)))
    for i in range(4):
        x, y = 5.5 + i * 2.7, 13.2 - i * 2.7
        p.drawLine(QPointF(x, y), QPointF(x + 1.6, y + 1.6))


def _table(p):
    p.drawRect(QRectF(3, 4, 14, 12))
    p.drawLine(QPointF(3, 8), QPointF(17, 8))
    p.drawLine(QPointF(3, 12), QPointF(17, 12))
    p.drawLine(QPointF(8, 4), QPointF(8, 16))


def _export(p):
    p.drawPolyline(_poly((4, 11), (4, 17), (16, 17), (16, 11)))
    p.drawLine(QPointF(10, 13), QPointF(10, 3))
    p.drawPolyline(_poly((6.5, 6.5), (10, 3), (13.5, 6.5)))


def _settings(p):
    p.drawEllipse(QPointF(10, 10), 2.6, 2.6)
    path = QPainterPath()
    import math
    for i in range(8):
        a = math.radians(i * 45)
        path.moveTo(10 + 5.2 * math.cos(a), 10 + 5.2 * math.sin(a))
        path.lineTo(10 + 7.6 * math.cos(a), 10 + 7.6 * math.sin(a))
    p.drawPath(path)
    p.drawEllipse(QPointF(10, 10), 5.2, 5.2)


def _play(p):
    p.setBrush(p.pen().color())
    p.drawPolygon(_poly((6, 4), (16, 10), (6, 16)))


def _pause(p):
    p.setBrush(p.pen().color())
    p.drawRect(QRectF(5, 4, 3.4, 12))
    p.drawRect(QRectF(11.6, 4, 3.4, 12))


def _prev(p):
    p.drawPolyline(_poly((12.5, 4.5), (7, 10), (12.5, 15.5)))


def _next(p):
    p.drawPolyline(_poly((7.5, 4.5), (13, 10), (7.5, 15.5)))


def _send(p):
    p.drawLine(QPointF(10, 16), QPointF(10, 4))
    p.drawPolyline(_poly((5, 9), (10, 4), (15, 9)))


def _stop(p):
    p.setBrush(p.pen().color())
    p.drawRect(QRectF(5.5, 5.5, 9, 9))


def _trash(p):
    p.drawLine(QPointF(4, 6), QPointF(16, 6))
    p.drawPolyline(_poly((5.5, 6), (6.5, 17), (13.5, 17), (14.5, 6)))
    p.drawPolyline(_poly((8, 6), (8, 3.5), (12, 3.5), (12, 6)))


def _check(p):
    p.drawPolyline(_poly((4, 10.5), (8.2, 14.5), (16, 5.5)))


def _cross(p):
    p.drawLine(QPointF(5, 5), QPointF(15, 15))
    p.drawLine(QPointF(15, 5), QPointF(5, 15))


def _plus(p):
    p.drawLine(QPointF(10, 4), QPointF(10, 16))
    p.drawLine(QPointF(4, 10), QPointF(16, 10))


_ICONS = {
    "picture": _picture, "plan": _plan, "terrain": _terrain, "intel": _intel, "imagery": _imagery,
    "sensors": _sensors, "draw": _draw, "tools": _tools, "select": _select, "pan": _pan,
    "zoom_in": lambda p: _zoom(p, True), "zoom_out": lambda p: _zoom(p, False), "extent": _extent,
    "measure": _measure, "table": _table, "export": _export, "settings": _settings, "play": _play,
    "pause": _pause, "prev": _prev, "next": _next, "send": _send, "stop": _stop, "trash": _trash,
    "check": _check, "cross": _cross, "plus": _plus,
}


def pixmap(name, size=20, color="#c9cbd1"):
    scale = 2                                    # render at 2x for crisp edges on high-DPI screens
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    k = size * scale / GRID
    p.scale(k, k)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _ICONS[name](p)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name, color="#c9cbd1", active="#ffffff"):
    ic = QIcon()
    ic.addPixmap(pixmap(name, 20, color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, 20, active), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, 20, active), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, 20, "#6a6c73"), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic
