"""Application bootstrap: a standalone QGIS-engine app (not a QGIS plugin)."""
import os
import sys

_app = None


def _prefix():
    if os.environ.get("QGIS_PREFIX_PATH"):
        return os.environ["QGIS_PREFIX_PATH"]
    if sys.platform.startswith("win"):
        root = os.environ.get("OSGEO4W_ROOT", r"C:\OSGeo4W")
        return os.path.join(root, "apps", "qgis")
    if sys.platform == "darwin":
        return "/Applications/QGIS.app/Contents/MacOS"
    return "/usr"


def init_qgis(gui=True):
    """Create the QgsApplication, load providers and every processing algorithm."""
    global _app
    if _app is not None:
        return _app
    from qgis.core import QgsApplication
    from qgis.PyQt.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName("BADR")
    QCoreApplication.setApplicationName("BADR2")
    QgsApplication.setPrefixPath(_prefix(), True)
    _app = QgsApplication([sys.argv[0].encode()] if sys.argv else [], gui)
    _app.initQgis()

    # QGIS' own Processing framework: gives us native, GDAL and (if present)
    # GRASS/SAGA algorithms without re-implementing any of them.
    for cand in (os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"),
                 os.path.join(_prefix(), "share", "qgis", "python", "plugins"),
                 os.path.join(_prefix(), "python", "plugins")):
        if os.path.isdir(cand) and cand not in sys.path:
            sys.path.append(cand)
    try:
        from processing.core.Processing import Processing
        Processing.initialize()
    except Exception as exc:  # pragma: no cover
        print(f"[BADR] processing framework unavailable: {exc}", file=sys.stderr)

    from .symbology.engine import register_expressions
    register_expressions()
    return _app


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="badr", description="BADR 2 - AI-first military planning GIS")
    ap.add_argument("operation", nargs="?", help="operation folder to open")
    ap.add_argument("--demo", action="store_true", help="load a fictional training scenario")
    ap.add_argument("--mode", help="panel to show at start (PICTURE, PLAN, TERRAIN, INTEL, IMAGERY, SENSORS, TOOLS)")
    ap.add_argument("--screenshot", metavar="PNG", help="render the window to a PNG and exit")
    args = ap.parse_args(argv)

    app = init_qgis(gui=True)
    from .ui.main_window import MainWindow
    win = MainWindow(args.operation)
    if args.demo:
        from .demo import load_demo
        load_demo(win)
    if args.mode:
        names = [m[0] for m in __import__("badr.ui.panels", fromlist=["MODES"]).MODES]
        if args.mode.upper() in names:
            i = names.index(args.mode.upper())
            win.mode_group.button(i).setChecked(True)
            win.stack.setCurrentIndex(i)
    win.show()
    if args.screenshot:
        from qgis.PyQt.QtCore import QTimer

        def grab():
            win.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(4500, grab)
    rc = app.exec() if hasattr(app, "exec") else app.exec_()
    app.exitQgis()
    return rc
