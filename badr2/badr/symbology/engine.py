"""NATO APP-6D symbol rendering for QGIS.

Two back ends:

* **milsymbol** (bundled, MIT) run inside Qt's QJSEngine: full APP-6D /
  MIL-STD-2525D coverage including text amplifiers, echelons, status bars and
  direction arrows.
* a small **native fallback** (frame + a few icons) used when QtQml is not
  installed, and from render threads while the real symbol is being produced.

Symbols reach the map through two QGIS expression functions registered here:
``milsym(...)`` returns a ``base64:`` SVG for an SVG marker's data-defined
path, ``milsym_size(...)`` its width, so that every frame is drawn at the same
scale no matter how much amplifier text surrounds it.  Because this is plain
QGIS symbology the symbols also work in print layouts and exports.
"""
import base64
import json
import os
import re
import threading

from qgis.PyQt.QtCore import QObject, QThread, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication

from . import sidc as S

BASE_SIZE = 30          # milsymbol "size" option; frame height is roughly this in px
OPT_FIELDS = ("name", "higher", "info", "dtg", "heading", "speed", "strength")
_MS_OPTS = {"name": "uniqueDesignation", "higher": "higherFormation", "info": "additionalInformation",
            "dtg": "dtg", "heading": "direction", "speed": "speed", "strength": "reinforcedReduced"}


def _load_js_engine():
    QJSEngine = None
    for mod in ("qgis.PyQt.QtQml", "PyQt5.QtQml", "PyQt6.QtQml"):
        try:
            QJSEngine = __import__(mod, fromlist=["QJSEngine"]).QJSEngine
            break
        except Exception:
            continue
    if QJSEngine is None:
        return None
    path = os.path.join(os.path.dirname(__file__), "vendor", "milsymbol.js")
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return None
    eng = QJSEngine()
    res = eng.evaluate("var module={exports:{}};var exports=module.exports;\n" + src +
                       "\n;var ms=module.exports;", "milsymbol.js")
    if res.isError():
        return None
    eng.evaluate(
        "function badrRender(sidc, optsJson){var o=JSON.parse(optsJson);"
        "var s=new ms.Symbol(sidc,o);var a=s.getAnchor();var z=s.getSize();"
        "return JSON.stringify({svg:s.asSVG(),ax:a.x,ay:a.y,w:z.width,h:z.height,valid:s.isValid()});}")
    return eng


def _center(svg, ax, ay, w, h):
    """Re-wrap an SVG so its anchor point sits at the image centre."""
    m = re.search(r"viewBox=\"([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)\"", svg)
    if m:
        minx, miny, vbw = float(m.group(1)), float(m.group(2)), float(m.group(3))
    else:
        minx, miny, vbw = 0.0, 0.0, w
    k = w / vbw if vbw else 1.0          # viewBox units -> px (milsymbol draws on a 100-unit grid)
    inner = svg[svg.index(">") + 1: svg.rindex("</svg>")]
    W = 2 * max(ax, w - ax) + 2
    H = 2 * max(ay, h - ay) + 2
    dx = W / 2 - ax
    dy = H / 2 - ay
    out = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny" '
           f'width="{W:.1f}" height="{H:.1f}" viewBox="0 0 {W:.1f} {H:.1f}">'
           f'<g transform="translate({dx:.2f},{dy:.2f}) scale({k:.5f}) translate({-minx:.2f},{-miny:.2f})">'
           f'{inner}</g></svg>')
    return out, W, H


# ---------------------------------------------------------------------------
# Native fallback renderer
# ---------------------------------------------------------------------------
_FILL = {"friend": "#80E0FF", "assumed_friend": "#80E0FF", "neutral": "#AAFFAA",
         "hostile": "#FF8080", "suspect": "#FF8080", "unknown": "#FFFF80", "pending": "#FFFF80"}
_ECH = {"11": "Ø", "12": "•", "13": "••", "14": "•••", "15": "|", "16": "||", "17": "|||",
        "18": "X", "21": "XX", "22": "XXX", "23": "XXXX"}
_ICON = {  # entity prefix -> svg fragment inside a 100x70 box centred on 0,0
    "1211": '<path d="M-50,-35 L50,35 M-50,35 L50,-35"/>',                     # infantry
    "1205": '<ellipse cx="0" cy="0" rx="32" ry="16"/>',                       # armour
    "1213": '<path d="M-50,35 L50,-35"/>',                                    # recce
    "1303": '<circle cx="0" cy="0" r="9" fill="#000"/>',                      # artillery
    "1407": '<path d="M-30,12 V-12 H30 V12 M0,-12 V8"/>',                     # engineer
    "1613": '<path d="M0,-35 V35 M-50,0 H50"/>',                              # medical
}


def fallback_svg(sidc, opts):
    try:
        p = S.parse(sidc)
    except ValueError:
        p = {"affiliation": "unknown", "symbol_set": "10", "echelon": "00", "entity": "000000", "status": "0"}
    aff = p["affiliation"]
    fill = _FILL.get(aff, "#FFFF80")
    dash = ' stroke-dasharray="8,5"' if p["status"] == "1" or aff in ("pending", "assumed_friend", "suspect") else ""
    if aff in ("hostile", "suspect"):
        frame = '<path d="M0,-52 L52,0 L0,52 L-52,0 Z"'
    elif aff == "neutral":
        frame = '<rect x="-42" y="-42" width="84" height="84"'
    elif aff in ("unknown", "pending"):
        frame = ('<path d="M-30,-30 C-30,-65 30,-65 30,-30 C65,-30 65,30 30,30 '
                 'C30,65 -30,65 -30,30 C-65,30 -65,-30 -30,-30 Z"')
    else:
        frame = '<rect x="-55" y="-37" width="110" height="74"'
    icon = _ICON.get(p["entity"][:4], "") if p["symbol_set"] == "10" else ""
    if not icon:
        label = {"40": "ACT", "20": "INST", "15": "EQPT", "11": "CIV", "01": "AIR"}.get(p["symbol_set"], "")
        icon = (f'<text x="0" y="7" text-anchor="middle" font-family="Arial" font-size="20" '
                f'font-weight="bold" stroke="none" fill="#000">{label}</text>') if label else ""
    ech = _ECH.get(p["echelon"], "")
    name = str(opts.get("name") or "")[:18].replace("&", "&amp;").replace("<", "&lt;")
    scale = BASE_SIZE / 100.0
    W, H = 300 * scale, 190 * scale
    body = (
        f'{frame} fill="{fill}" stroke="#000" stroke-width="5"{dash}/>'
        f'<g fill="none" stroke="#000" stroke-width="5">{icon}</g>'
        f'<text x="0" y="-60" text-anchor="middle" font-family="Arial" font-size="26" font-weight="bold">{ech}</text>'
        f'<text x="64" y="50" font-family="Arial" font-size="26" stroke="#fff" stroke-width="5">{name}</text>'
        f'<text x="64" y="50" font-family="Arial" font-size="26">{name}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny" width="{W:.1f}" '
           f'height="{H:.1f}" viewBox="-150 -95 300 190">{body}</svg>')
    return svg, W, H


# ---------------------------------------------------------------------------
class SymbolEngine(QObject):
    """Thread-safe SIDC -> SVG cache."""

    rendered = pyqtSignal()            # new symbols are available: repaint
    _request = pyqtSignal(str, str)    # (sidc, opts-json) marshalled to the GUI thread

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._cache = {}        # key -> (b64, width, height)
        self._provisional = {}
        self._js = _load_js_engine()
        self._request.connect(self._on_request)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.rendered)

    @property
    def backend(self):
        return "milsymbol (APP-6D)" if self._js else "native fallback"

    @staticmethod
    def _key(sidc, opts):
        return f"{sidc}|{json.dumps(opts, sort_keys=True, default=str)}"

    @staticmethod
    def clean_opts(opts):
        out = {}
        for k in OPT_FIELDS:
            v = (opts or {}).get(k)
            if v is None or v == "" or str(v) == "NULL":
                continue
            if k in ("heading", "speed"):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if k == "heading":
                    v = round(v / 5.0) * 5 % 360       # 5-degree steps keep the cache small for moving tracks
                else:
                    v = f"{v:.0f} km/h"
            else:
                v = str(v)
            out[k] = v
        return out

    def _on_gui_thread(self):
        app = QApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()

    def _render_js(self, sidc, opts):
        ms = {"size": BASE_SIZE, "outlineWidth": 3, "outlineColor": "rgba(255,255,255,0.85)",
              "infoSize": 34, "strokeWidth": 3.5}
        for k, v in opts.items():
            ms[_MS_OPTS[k]] = v
        # arguments are passed as JSON literals, so no user text is ever evaluated as code
        res = self._js.evaluate(f"badrRender({json.dumps(str(sidc))},{json.dumps(json.dumps(ms))})")
        if res.isError():
            return None
        d = json.loads(res.toString())
        return _center(d["svg"], d["ax"], d["ay"], d["w"], d["h"])

    def render(self, sidc, opts=None):
        """Return (base64_svg, width_px, height_px) for a symbol."""
        sidc = str(sidc or "")
        opts = self.clean_opts(opts)
        key = self._key(sidc, opts)
        with self._lock:
            hit = self._cache.get(key)
            if hit:
                return hit
        if self._js is not None and self._on_gui_thread():
            out = None
            if S.is_valid(sidc):
                try:
                    out = self._render_js(sidc, opts)
                except Exception:
                    out = None
            if out is None:
                out = fallback_svg(sidc, opts)
            val = (base64.b64encode(out[0].encode("utf-8")).decode("ascii"), out[1], out[2])
            with self._lock:
                if len(self._cache) > 6000:            # bound memory on long-running live feeds
                    self._cache.clear()
                    self._provisional.clear()
                self._cache[key] = val
            return val
        with self._lock:
            prov = self._provisional.get(key)
        if prov is None:
            svg, w, h = fallback_svg(sidc, opts)
            prov = (base64.b64encode(svg.encode("utf-8")).decode("ascii"), w, h)
            with self._lock:
                if self._js is None:
                    self._cache[key] = prov
                else:
                    self._provisional[key] = prov
            if self._js is not None:
                self._request.emit(sidc, json.dumps(opts))
        return prov

    def _on_request(self, sidc, opts_json):
        self.render(sidc, json.loads(opts_json))
        self._timer.start()

    def svg(self, sidc, opts=None):
        return base64.b64decode(self.render(sidc, opts)[0]).decode("utf-8")

    def pixmap(self, sidc, size=40, opts=None):
        """QPixmap for palettes and lists (GUI thread)."""
        from qgis.PyQt.QtCore import QByteArray, QRectF, Qt
        from qgis.PyQt.QtGui import QPainter, QPixmap
        from qgis.PyQt.QtSvg import QSvgRenderer
        b64, w, h = self.render(sidc, opts)
        r = QSvgRenderer(QByteArray(base64.b64decode(b64)))
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        k = min(size / w, size / h)
        r.render(p, QRectF((size - w * k) / 2, (size - h * k) / 2, w * k, h * k))
        p.end()
        return pm


# ---------------------------------------------------------------------------
# QGIS expression functions
# ---------------------------------------------------------------------------
_registered = False
_keepalive = []


def register_expressions():
    global _registered
    if _registered:
        return
    from qgis.core import QgsExpression, qgsfunction

    def _opts(values):
        return dict(zip(OPT_FIELDS, values[1:]))

    @qgsfunction(args=-1, group="BADR", register=False, handlesnull=True)
    def milsym(values, feature, parent):
        """milsym(sidc, name, higher, info, dtg, heading, speed, strength) -> base64 SVG of the NATO symbol."""
        return "base64:" + SymbolEngine.instance().render(values[0], _opts(values))[0]

    @qgsfunction(args=-1, group="BADR", register=False, handlesnull=True)
    def milsym_size(values, feature, parent):
        """milsym_size(sidc, name, ...) -> symbol image width in px."""
        return float(SymbolEngine.instance().render(values[0], _opts(values))[1])

    for fn in (milsym, milsym_size):
        QgsExpression.registerFunction(fn)
        _keepalive.append(fn)      # QGIS holds a raw pointer; Python must keep the object
    _registered = True
