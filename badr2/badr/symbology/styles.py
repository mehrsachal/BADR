"""QGIS renderers and labels for the COP layers."""
from qgis.core import (
    Qgis, QgsFillSymbol, QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsProperty, QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer,
    QgsSymbolLayer, QgsTextBufferSettings, QgsTextFormat, QgsUnitTypes,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

from .palette import COLORS, GRAPHIC_KINDS

MM_PER_PX = 0.3        # symbol image px -> map mm at symbol scale 1.0

# Amplifiers drawn on the map are kept to what a planner reads at a glance;
# DTG and remarks stay in the attributes (and in the AI's view of the picture).
_SYM_ARGS = '"sidc","name","higher","info",NULL,"heading","speed","strength"'
_TRACK_ARGS = '"sidc","name",NULL,"info",NULL,"heading","speed"'
_INCIDENT_ARGS = '"sidc","name"'
_DETECTION_ARGS = '"sidc","label",NULL,to_string(round("confidence"*100))||\'%\''


def _pending_ring():
    ring = QgsSimpleMarkerSymbolLayer()
    ring.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    ring.setColor(QColor(255, 176, 0, 40))
    ring.setStrokeColor(QColor("#FFB000"))
    ring.setStrokeWidth(0.7)
    ring.setStrokeStyle(Qt.PenStyle.DashLine)
    ring.setSize(15)
    ring.setDataDefinedProperty(QgsSymbolLayer.PropertyLayerEnabled,
                                QgsProperty.fromExpression("\"review\" = 'pending'"))
    return ring


def milsym_marker(args, scale=1.0, review=True, stale_field=None):
    """Marker symbol whose image is the feature's NATO symbol."""
    svg = QgsSvgMarkerSymbolLayer("")
    svg.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    svg.setDataDefinedProperty(QgsSymbolLayer.PropertyName,
                               QgsProperty.fromExpression(f"milsym({args})"))
    svg.setDataDefinedProperty(QgsSymbolLayer.PropertySize,
                               QgsProperty.fromExpression(f"milsym_size({args}) * {MM_PER_PX * scale}"))
    sym = QgsMarkerSymbol()
    sym.changeSymbolLayer(0, svg)
    if review:
        sym.insertSymbolLayer(0, _pending_ring())
    if stale_field:
        svg.setDataDefinedProperty(QgsSymbolLayer.PropertyOpacity,
                                   QgsProperty.fromExpression(f'if("{stale_field}" = 1, 45, 100)'))
    return sym


def _case(mapping, default, field="kind"):
    parts = " ".join(f"WHEN \"{field}\" = '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"CASE {parts} ELSE '{default}' END"


def _color_expr():
    by_kind = {k: COLORS[v[1]] for k, v in GRAPHIC_KINDS.items()}
    return ("CASE WHEN \"affiliation\" IN ('hostile','suspect','enemy') THEN '%s' "
            "WHEN \"affiliation\" = 'neutral' THEN '%s' ELSE %s END"
            % (COLORS["hostile"], COLORS["neutral"], _case(by_kind, COLORS["friend"])))


def _dash_expr():
    dashed = [k for k, v in GRAPHIC_KINDS.items() if "dash" in v[2]]
    quoted = ",".join(f"'{k}'" for k in dashed)
    return f"if(\"kind\" IN ({quoted}) OR \"status\" = 'planned' OR \"review\" = 'pending', 'dash', 'solid')"


def _label_expr():
    abbr = {k: v[0] for k, v in GRAPHIC_KINDS.items() if v[0]}
    return f"trim({_case(abbr, '')} || ' ' || coalesce(\"name\", ''))"


def _text_format(size=9, color="#FFFFFF", bold=True):
    fmt = QgsTextFormat()
    font = QFont("DejaVu Sans")
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(0, 0, 0, 200))
    fmt.setBuffer(buf)
    return fmt


def _labels(layer, expr, placement, size=9):
    pal = QgsPalLayerSettings()
    pal.fieldName = expr
    pal.isExpression = True
    pal.placement = placement
    pal.setFormat(_text_format(size))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)


def style_lines(layer):
    line = QgsSimpleLineSymbolLayer()
    line.setWidth(0.8)
    line.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromExpression(_color_expr()))
    line.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeStyle, QgsProperty.fromExpression(_dash_expr()))
    line.setDataDefinedProperty(
        QgsSymbolLayer.PropertyStrokeWidth,
        QgsProperty.fromExpression("CASE WHEN \"kind\" IN ('axis_of_advance','route') THEN 1.3 "
                                   "WHEN \"kind\" IN ('los_visible','los_hidden') THEN 1.1 ELSE 0.8 END"))
    sym = QgsLineSymbol()
    sym.changeSymbolLayer(0, line)

    head = QgsSimpleMarkerSymbolLayer()
    head.setShape(QgsSimpleMarkerSymbolLayer.ArrowHeadFilled)
    head.setSize(4.5)
    head.setStrokeStyle(Qt.PenStyle.NoPen)
    head.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, QgsProperty.fromExpression(_color_expr()))
    head_sym = QgsMarkerSymbol()
    head_sym.changeSymbolLayer(0, head)
    arrow = QgsMarkerLineSymbolLayer()
    try:
        arrow.setPlacements(Qgis.MarkerLinePlacement.LastVertex)
        arrow.setRotateSymbols(True)
    except AttributeError:                       # older QGIS
        arrow.setPlacement(QgsMarkerLineSymbolLayer.LastVertex)
        arrow.setRotateMarker(True)
    arrow.setSubSymbol(head_sym)
    arrows = ",".join(f"'{k}'" for k, v in GRAPHIC_KINDS.items() if v[2].startswith("arrow"))
    arrow.setDataDefinedProperty(QgsSymbolLayer.PropertyLayerEnabled,
                                 QgsProperty.fromExpression(f"\"kind\" IN ({arrows})"))
    sym.appendSymbolLayer(arrow)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    _labels(layer, _label_expr(), QgsPalLayerSettings.Line)


def style_areas(layer):
    fill = QgsSimpleFillSymbolLayer()
    fill.setStrokeWidth(0.8)
    fill.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor, QgsProperty.fromExpression(_color_expr()))
    fill.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeStyle, QgsProperty.fromExpression(_dash_expr()))
    fill.setDataDefinedProperty(
        QgsSymbolLayer.PropertyFillColor,
        QgsProperty.fromExpression(
            f"set_color_part({_color_expr()}, 'alpha', "
            "CASE WHEN \"kind\" IN ('hotspot','restricted','hlz_candidate') THEN 60 "
            "WHEN \"kind\" IN ('grg','range_ring','ao') THEN 0 ELSE 22 END)"))
    sym = QgsFillSymbol()
    sym.changeSymbolLayer(0, fill)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    _labels(layer, _label_expr(), QgsPalLayerSettings.Horizontal)


def apply_styles(operation, symbol_scale=1.0):
    """Attach renderers to every layer of an Operation."""
    L = operation.layers
    L["units"].setRenderer(QgsSingleSymbolRenderer(milsym_marker(_SYM_ARGS, symbol_scale)))
    L["incidents"].setRenderer(QgsSingleSymbolRenderer(milsym_marker(_INCIDENT_ARGS, symbol_scale * 0.85)))
    L["detections"].setRenderer(QgsSingleSymbolRenderer(milsym_marker(_DETECTION_ARGS, symbol_scale * 0.85)))
    L["tracks"].setRenderer(QgsSingleSymbolRenderer(
        milsym_marker(_TRACK_ARGS, symbol_scale * 0.9, review=False, stale_field="stale")))
    style_lines(L["lines"])
    style_areas(L["areas"])

    trail = QgsLineSymbol.createSimple({"color": "#7FE8FF", "width": "0.4", "line_style": "dot"})
    L["trails"].setRenderer(QgsSingleSymbolRenderer(trail))
    fp = QgsFillSymbol.createSimple({"color": "255,230,80,45", "outline_color": "#FFE650",
                                     "outline_width": "0.4", "outline_style": "dash"})
    L["footprints"].setRenderer(QgsSingleSymbolRenderer(fp))
    for layer in L.values():
        layer.triggerRepaint()
