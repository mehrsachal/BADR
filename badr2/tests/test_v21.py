"""2.1: selection and attributes, right-click, review buttons, export, custom layers,
persistence, story playback, background tasks, zero-token small talk."""
import os
import time

import pytest
from qgis.core import QgsCoordinateTransform, QgsPointXY, QgsRectangle, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest

LAT, LON = 35.262, -116.684


@pytest.fixture()
def win(qgis_app, tmp_path):
    from badr.core.settings import Settings
    from badr.ui.main_window import MainWindow
    Settings.set("ai/anthropic/key", "")
    Settings.set("ai/provider", "anthropic")
    w = MainWindow(str(tmp_path / "op"))
    w.show()
    w.wb.zoom_to(LAT, LON, 4000)
    qgis_app.processEvents()
    yield w
    w.close()


def pump(app, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def map_point(win, lat, lon):
    tr = QgsCoordinateTransform(win.wb.wgs84, win.canvas.mapSettings().destinationCrs(), win.wb.project)
    return tr.transform(QgsPointXY(lon, lat))


def add_unit(win, lat=LAT, lon=LON, name="A COY", **kw):
    return win.wb.operation.add_point("units", lat, lon, {"sidc": "10031000151211000000", "name": name}, **kw)


# ---- select, inspect, edit, delete -------------------------------------------
def test_click_selects_and_inspector_edits_attributes(win, qgis_app):
    uid = add_unit(win)
    win._select_at(map_point(win, LAT, LON))
    assert len(win._selection) == 1 and win.inspector.isVisible()
    assert "A COY" in win.inspector.title.text() and "Infantry" in win.inspector.title.text()
    assert win.wb.operation.layer("units").selectedFeatureCount() == 1 and len(win._bands) == 1
    rows = {win.inspector.table.item(r, 0).text(): r for r in range(win.inspector.table.rowCount())}
    win.inspector.table.item(rows["name"], 1).setText("B COY")           # edit in place
    assert win.wb.operation.find("units", uid)["name"] == "B COY"
    uid_item = win.inspector.table.item(rows["uid"], 1)
    assert not (uid_item.flags() & Qt.ItemFlag.ItemIsEditable)           # identifiers are read-only
    win._select_at(map_point(win, LAT + 0.02, LON + 0.02))               # click on empty ground
    assert win._selection == [] and not win.inspector.isVisible()


def test_box_select_and_delete(win, qgis_app):
    for i in range(3):
        add_unit(win, LAT + i * 0.002, LON, f"U{i}")
    add_unit(win, LAT + 0.03, LON + 0.03, "FAR")
    a, b = map_point(win, LAT - 0.001, LON - 0.001), map_point(win, LAT + 0.005, LON + 0.001)
    win._select_box(QgsRectangle(a, b))
    assert len(win._selection) == 3 and not win.inspector.isVisible()
    win.delete_selected()
    assert [f["name"] for f in win.wb.operation.layer("units").getFeatures()] == ["FAR"]


def test_user_layer_features_are_selectable_and_areas_hit_on_outline(win):
    from badr.core import exchange
    lyr = exchange.create_user_layer(win.wb, "Notes", "point", "#ff0000")
    exchange.add_user_feature(win.wb, lyr, [(LAT, LON)], "Well")
    win.manual.call("draw_graphic", {"kind": "nai", "points": [[LAT, LON]], "radius_m": 800, "name": "1"})
    win._select_at(map_point(win, LAT, LON))
    assert win._selection[0][0] is lyr                                   # the point wins over the area around it
    from badr.core.geodesy import destination
    edge = destination(LAT, LON, 90, 800)
    win._select_at(map_point(win, *edge))
    assert win._selection[0][0] is win.wb.operation.layer("areas")


# ---- right-click -----------------------------------------------------------------
def test_context_menu_offers_feature_and_location_actions(win):
    add_unit(win, source="ai")
    menu = win.build_context_menu(map_point(win, LAT, LON))
    labels = [a.text() for a in menu.actions()]
    assert "A COY" in labels[0]
    for expected in ("Attributes", "Zoom to", "Accept", "Reject", "Delete", "Viewshed from here", "Cordon here"):
        assert expected in labels, expected
    assert any(l.startswith("Copy 11S") for l in labels)
    empty = [a.text() for a in win.build_context_menu(map_point(win, LAT + 0.02, LON)).actions()]
    assert "Delete" not in empty and "Viewshed from here" in empty


def test_right_click_on_the_map_tool_raises_the_menu_signal(win, qgis_app):
    seen = []
    win.select_tool.context.disconnect()
    win.select_tool.context.connect(lambda mp, gp: seen.append(mp))
    QTest.mouseClick(win.canvas.viewport(), Qt.MouseButton.RightButton, pos=win.canvas.viewport().rect().center())
    assert pump(qgis_app, lambda: bool(seen), 2)


# ---- review queue: real button clicks -------------------------------------------
def test_accept_and_reject_buttons_work_by_clicking(win, qgis_app):
    a = add_unit(win, name="CACHE", source="ai")
    b = add_unit(win, LAT + 0.002, LON, name="CELL", source="ai")
    assert win.review_box.isVisible() and win.review_list.count() == 2
    QTest.mouseClick(win.btn_accept, Qt.MouseButton.LeftButton)
    assert win.wb.operation.find("units", a)["review"] == "confirmed"
    assert win.review_list.count() == 1 and "Accepted: CACHE" in win.feed.toPlainText()
    QTest.mouseClick(win.btn_reject, Qt.MouseButton.LeftButton)
    assert win.wb.operation.find("units", b) is None
    assert not win.review_box.isVisible() and "Rejected: CELL" in win.feed.toPlainText()


# ---- export ---------------------------------------------------------------------
@pytest.mark.parametrize("fmt,ext,count", [("gpkg", ".gpkg", 1), ("shp", ".shp", 2), ("geojson", ".geojson", 2),
                                           ("kml", ".kml", 2), ("csv", ".csv", 2)])
def test_export_to_gis_formats(win, tmp_path, fmt, ext, count):
    from badr.core import exchange
    add_unit(win)
    win.manual.call("draw_graphic", {"kind": "route", "points": [[LAT, LON], [LAT + 0.01, LON]], "name": "IRON"})
    op = win.wb.operation
    files = exchange.export_layers([op.layer("units"), op.layer("lines"), op.layer("incidents")],
                                   str(tmp_path / "x"), fmt, "Op X")
    assert len(files) == count and all(f.endswith(ext) and os.path.getsize(f) > 0 for f in files)
    if fmt == "gpkg":
        back = QgsVectorLayer(f"{files[0]}|layername=units_equipment", "u", "ogr")
        assert back.isValid() and back.featureCount() == 1
        assert next(back.getFeatures())["sidc"] == "10031000151211000000"     # symbology survives as SIDC
    if fmt == "geojson":
        assert QgsVectorLayer(files[0], "u", "ogr").featureCount() == 1


def test_export_dialog_defaults_to_the_picture(win):
    from badr.ui.widgets import ExportDialog
    add_unit(win)
    dlg = ExportDialog(win.wb, None, win)
    assert [l.name() for l in dlg.chosen()] == ["Units & Equipment"]
    assert dlg.format.count() >= 6


# ---- custom layers + persistence ---------------------------------------------------
def test_draw_panel_creates_layers_and_everything_survives_reopen(win, qgis_app, tmp_path):
    from badr.core import exchange
    from badr.core.workbench import Workbench
    panel = win.panels["DRAW"]
    panel.quick("Notes (points)", "point", "#ffd24a")                    # creates the layer and arms the tool
    assert panel.layer.count() == 1 and win.tool_hint.isVisible()
    win.cancel_tool()
    layer = panel.current()
    exchange.add_user_feature(win.wb, layer, [(LAT, LON)], "Water point", "seasonal")
    uid = add_unit(win, name="A COY")
    win.wb.operation.update("units", uid, {"remarks": "moved"})
    folder = win.wb.operation.folder
    assert not os.path.exists(os.path.join(folder, "cop.gpkg"))          # nothing written yet (debounced)
    win.wb.operation.close()
    assert os.path.exists(os.path.join(folder, "cop.gpkg"))

    wb2 = Workbench()
    wb2.open_operation(folder)
    units = wb2.operation.layer("units")
    assert units.providerType() == "memory" and units.featureCount() == 1
    f = next(units.getFeatures())
    assert (f["name"], f["remarks"], f["uid"]) == ("A COY", "moved", uid)
    (restored,) = exchange.user_layers(wb2)
    assert restored.name() == "Notes" and restored.featureCount() == 1
    assert next(restored.getFeatures())["notes"] == "seasonal"
    assert restored.customProperty("badr/color") == "#ffd24a"


def test_autosave_fires_after_edits(win, qgis_app):
    win.wb.operation.AUTOSAVE_MS = 50
    win.wb.operation._timer.setInterval(50)
    add_unit(win)
    path = os.path.join(win.wb.operation.folder, "cop.gpkg")
    assert pump(qgis_app, lambda: os.path.exists(path), 3)
    assert not os.path.exists(os.path.join(win.wb.operation.folder, "cop.saving.gpkg"))


def test_attribute_table_opens_for_any_layer(win):
    add_unit(win)
    win.open_table(win.wb.operation.layer("units"))
    dlg = win._tables[win.wb.operation.layer("units").id()]
    assert dlg.isVisible() and "Units" in dlg.windowTitle()
    win.wb.operation.layer("units").selectAll()
    assert dlg.count.text().startswith("1 selected")
    dlg.close()


# ---- assistant: free small talk, story playback -------------------------------------
def test_greeting_costs_no_tokens(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.agent, "run", lambda *a, **k: calls.append(a))
    for text in ("hi", "Hello!", "thanks", "ok"):
        win.ask(text)
    assert calls == [] and "Ready." in win.feed.toPlainText() and not win._busy
    from badr.ai.quick import small_talk
    assert small_talk("hi there, where is the enemy?") is None           # real questions still go to the model


def test_story_plays_and_flies_the_map(win, qgis_app):
    uid = add_unit(win, LAT + 0.05, LON + 0.05, "OBJ FOX")
    beats = [{"say": "Threat is concentrated around OBJ FOX.", "lat": LAT + 0.05, "lon": LON + 0.05,
              "zoom_m": 800, "uid": uid},
             {"say": "Your patrol base is two kilometres south.", "lat": LAT, "lon": LON, "zoom_m": 1500},
             {"say": "No elevation is loaded."}]
    win.story.set_story(beats)
    assert win.story.isVisible() and win.story.list.count() == 3 and win.story.play_btn.isVisible()
    win.story.go(0)
    assert pump(qgis_app, lambda: win._fly is None, 4)
    c = win.wb.center_wgs84()
    assert abs(c[0] - (LAT + 0.05)) < 5e-4 and abs(c[1] - (LON + 0.05)) < 5e-4
    ext = win.wb.extent_wgs84()
    assert 0.010 < (ext[3] - ext[1]) < 0.02                              # zoomed to ~800 m radius
    assert win._selection and win._selection[0][0] is win.wb.operation.layer("units")
    win.story.play()
    assert win.story.playing and win.story.index == 1
    win.story.stop()
    assert not win.story.playing
    win.story.set_story([{"say": "Just words."}])
    assert not win.story.play_btn.isVisible()


def test_plain_text_answer_is_still_cut_into_beats(win):
    win._busy = True
    win._on_agent_event("final", "Route IRON is the more exposed. Seven IEDs lie within 300 m. Use COPPER.")
    assert [b["say"] for b in win.story.beats] == ["Route IRON is the more exposed.",
                                                   "Seven IEDs lie within 300 m.", "Use COPPER."]
    assert win._busy is False


# ---- background work and indicators ---------------------------------------------------
def test_elevation_downloads_off_the_gui_thread_with_indicator(win, qgis_app, monkeypatch):
    import threading
    import numpy as np
    from osgeo import gdal, osr
    from badr.analysis import dem
    seen = {}

    def fake_fetch(bbox, out, progress=None):
        seen["thread"] = threading.current_thread()
        time.sleep(0.15)
        ds = gdal.GetDriverByName("GTiff").Create(out, 20, 20, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((bbox[0], (bbox[2] - bbox[0]) / 20, 0, bbox[3], 0, -(bbox[3] - bbox[1]) / 20))
        srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())
        ds.GetRasterBand(1).WriteArray(np.full((20, 20), 500, dtype="float32"))
        ds = None
        return "test source"
    monkeypatch.setattr(dem, "fetch_elevation", fake_fetch)
    win.do("get_elevation")
    assert win.busy.active and win.busy.isVisible() and win.wb.dem() is None     # returned at once, still loading
    assert pump(qgis_app, lambda: win.wb.dem() is not None, 5)
    assert seen["thread"] is not threading.current_thread()
    assert not win.busy.active and "Elevation loaded from test source" in win.feed.toPlainText()


def test_background_failure_is_reported_and_clears_the_indicator(win, qgis_app):
    win.run_async("Trying", lambda: 1 / 0, lambda r: None)
    assert pump(qgis_app, lambda: not win.busy.active, 3)
    assert "division by zero" in win.feed.toPlainText()


def test_busy_indicator_counts_overlapping_tasks(qgis_app):
    from badr.ui.widgets import BusyIndicator
    b = BusyIndicator()
    t1, t2 = b.start("One"), b.start("Two")
    assert b.active and b.label.text().startswith("Two")
    b.stop(t2)
    assert b.active and b.label.text().startswith("One")
    b.stop(t1)
    assert not b.active


def test_splash_reports_progress(qgis_app):
    from badr.ui.widgets import Splash
    s = Splash()
    s.show()
    s.step("Loading analysis tools", 30)
    assert s.bar.value() == 30 and s.label.text() == "Loading analysis tools"
    s.close()


def test_measure_tool_reports_distance_and_bearing(win, qgis_app):
    from badr.ui.map_tools import MeasureTool
    tool = MeasureTool(win.canvas, win.wb)
    got = []
    tool.measured.connect(lambda total, leg, brg: got.append((total, leg, brg)))
    tool.points = [(LAT, LON), (LAT + 0.01, LON)]
    tool._report()
    total, leg, brg = got[0]
    assert 1100 < total < 1125 and leg == total and (brg < 1 or brg > 359)
