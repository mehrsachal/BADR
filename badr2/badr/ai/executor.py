"""Escape hatch: run AI-written PyQGIS when no structured tool fits.

This is the one place free-form code runs, so it is gated: the UI asks the
operator to approve the code first (Settings > "confirm AI code").  A minimal
``iface`` stand-in is provided so ordinary PyQGIS snippets work in BADR's
standalone window.
"""
import io
import sys
import traceback


class IfaceShim:
    """Just enough of QgisInterface for scripts and the Processing dialogs."""

    def __init__(self, workbench, window=None):
        self._wb = workbench
        self._window = window

    def mapCanvas(self):
        return self._wb.canvas

    def mainWindow(self):
        return self._window

    def activeLayer(self):
        return self._wb.canvas.currentLayer() if self._wb.canvas else None

    def messageBar(self):
        return getattr(self._window, "message_bar", None)

    def addVectorLayer(self, path, name, provider="ogr"):
        from qgis.core import QgsVectorLayer
        return self._wb.add_layer(QgsVectorLayer(path, name, provider))

    def addRasterLayer(self, path, name="", provider="gdal"):
        from qgis.core import QgsRasterLayer
        return self._wb.add_layer(QgsRasterLayer(path, name, provider))

    def zoomToActiveLayer(self):
        layer = self.activeLayer()
        if layer:
            self._wb.zoom_to_layer(layer)

    def __getattr__(self, name):          # anything else: harmless no-op
        return lambda *a, **k: None


def run_pyqgis(code, workbench, iface=None):
    """Execute code on the GUI thread; returns (ok, output)."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        g = {"__name__": "__badr_exec__", "wb": workbench, "workbench": workbench,
             "operation": workbench.operation, "iface": iface or IfaceShim(workbench),
             "BADR_WORKSPACE": workbench.out_dir}
        exec("from qgis.core import *\nfrom qgis.gui import *\nimport processing, os, math, json\n", g)
        exec(code, g)
        workbench.refresh()
        return True, buf.getvalue().strip() or "Completed."
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        return False, f"{buf.getvalue()}\n{type(exc).__name__}: {exc}\n{tb}".strip()
    finally:
        sys.stdout = old
