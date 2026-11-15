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

    _init_processing()
    from .symbology.engine import register_expressions
    register_expressions()
    return _app


def _init_processing():
    """QGIS' own Processing framework: native, GDAL and (if present) GRASS/SAGA
    algorithms without re-implementing any of them."""
    from qgis.core import QgsApplication
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


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="badr", description="BADR 2 - AI-first military planning GIS")
    ap.add_argument("operation", nargs="?", help="operation folder to open")
    ap.add_argument("--demo", action="store_true", help="load a fictional training scenario")
    ap.add_argument("--mode", help="panel to show at start (PICTURE, PLAN, DRAW, TERRAIN, INTEL, IMAGERY, "
                                   "SENSORS, TOOLS)")
    ap.add_argument("--screenshot", metavar="PNG", help="render the window to a PNG and exit")
    args = ap.parse_args(argv)

    # 1. the bare Qt application comes up first so a splash can be shown at once
    from qgis.core import QgsApplication
    from qgis.PyQt.QtCore import QCoreApplication
    global _app
    QCoreApplication.setOrganizationName("BADR")
    QCoreApplication.setApplicationName("BADR2")
    QgsApplication.setPrefixPath(_prefix(), True)
    _app = QgsApplication([sys.argv[0].encode()] if sys.argv else [], True)
    from .ui import theme
    from .ui.widgets import Splash, release_native_splash
    _app.setStyleSheet(theme.QSS)
    splash = Splash()
    splash.show()
    splash.step("Starting the GIS engine", 8)
    release_native_splash()                 # the launcher's instant splash hands over to this one

    # 2. the slow parts, each reported on the progress bar
    _app.initQgis()
    splash.step("Loading analysis tools", 30)
    _init_processing()
    splash.step("Loading NATO symbology", 50)
    from .symbology.engine import SymbolEngine, register_expressions
    register_expressions()
    SymbolEngine.instance()
    splash.step("Opening the operation", 62)
    from .ui.main_window import MainWindow
    win = MainWindow(args.operation, progress=splash.step)
    if args.demo:
        splash.step("Loading the training scenario", 85)
        from .demo import load_demo
        load_demo(win)
    if args.mode:
        names = [m[0] for m in __import__("badr.ui.panels", fromlist=["MODES"]).MODES]
        if args.mode.upper() in names:
            win.show_mode(names.index(args.mode.upper()))
    splash.step("Ready", 100)
    if args.screenshot:
        win.show()
    else:
        win.showMaximized()          # planners want the whole screen for the map
    splash.close()
    if args.screenshot:
        from qgis.PyQt.QtCore import QTimer

        def grab():
            win.grab().save(args.screenshot)
            _app.quit()
        QTimer.singleShot(4500, grab)
    rc = _app.exec() if hasattr(_app, "exec") else _app.exec_()
    _app.exitQgis()
    return rc
