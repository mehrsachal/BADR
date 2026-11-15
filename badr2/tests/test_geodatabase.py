"""Catalog and geodatabase management: .gdb with feature datasets, feature classes, tables, domains."""
import os

import pytest
from qgis.core import QgsVectorLayer

from badr.core import geodatabase as G

LAT, LON = 35.262, -116.684


@pytest.fixture()
def gdb(tmp_path):
    return G.create(str(tmp_path / "ops.gdb"))


def test_create_file_geodatabase_and_geopackage(tmp_path, gdb):
    assert os.path.isdir(gdb) and G.kind(gdb) == "gdb"
    assert G.contents(gdb) == {"datasets": {}, "layers": [], "domains": {}}
    gpkg = G.create(str(tmp_path / "side.gpkg"))
    assert os.path.isfile(gpkg) and G.contents(gpkg)["layers"] == []
    assert G.create(str(tmp_path / "noext")).endswith("noext.gdb")
    with pytest.raises(G.GdbError):
        G.create(gdb)


def test_feature_dataset_holds_classes_sharing_a_coordinate_system(gdb):
    G.create_dataset(gdb, "Transport", 32611)
    c = G.contents(gdb)
    assert c["datasets"] == {"Transport": []}                       # empty datasets are remembered
    assert G.dataset_epsg(gdb, "Transport") == 32611
    G.create_feature_class(gdb, "routes", "line", 4326, "Transport", [{"name": "name", "type": "Text"}])
    G.create_feature_class(gdb, "bridges", "point", 3857, "Transport")
    c = G.contents(gdb)
    rows = {r["name"]: r for r in c["datasets"]["Transport"]}
    assert set(rows) == {"routes", "bridges"} and c["layers"] == []
    assert rows["routes"]["geometry"] == "line" and rows["bridges"]["geometry"] == "point"
    assert rows["routes"]["epsg"] == rows["bridges"]["epsg"] == 32611   # the dataset's system wins
    assert not os.path.exists(gdb + ".badr.json")                       # no longer "pending"
    with pytest.raises(G.GdbError):
        G.create_dataset(gdb, "Transport")


def test_feature_class_fields_aliases_domains_and_tables(gdb):
    G.create_domain(gdb, "Surface", {"P": "Paved", "G": "Gravel", "D": "Dirt"}, "Road surface")
    G.create_feature_class(gdb, "roads", "line", 4326, None, [
        {"name": "name", "type": "Text", "alias": "Road name"},
        {"name": "surface", "type": "Text", "domain": "Surface"},
        {"name": "lanes", "type": "Whole number"}, {"name": "width m", "type": "Decimal"},
        {"name": "surveyed", "type": "Date"}])
    G.create_feature_class(gdb, "contacts", "table", fields=[{"name": "callsign", "type": "text"}])
    info = G.layer_info(gdb, "roads")
    fields = {f["name"]: f for f in info["fields"]}
    assert list(fields) == ["name", "surface", "lanes", "width_m", "surveyed"]
    assert fields["name"]["alias"] == "Road name" and fields["surface"]["domain"] == "Surface"
    assert [fields[k]["type"] for k in ("lanes", "width_m")] == ["Whole number", "Decimal"]
    assert fields["surveyed"]["type"] in ("Date", "Date and time")      # file geodatabases store dates with a time
    c = G.contents(gdb)
    assert c["domains"]["Surface"] == {"P": "Paved", "G": "Gravel", "D": "Dirt"}
    assert {r["name"]: r["geometry"] for r in c["layers"]} == {"contacts": "table", "roads": "line"}


def test_validation_messages(gdb):
    G.create_feature_class(gdb, "notes", "point")
    for bad, kwargs in (("already exists", dict(name="NOTES")), ("reserved", dict(name="x", fields=[{"name": "OBJECTID"}])),
                        ("does not exist", dict(name="y", fields=[{"name": "s", "domain": "Nope"}])),
                        ("geometry must be", dict(name="z", geometry="blob")),
                        ("same name", dict(name="w", fields=[{"name": "a"}, {"name": "A"}])),
                        ("empty", dict(name="  "))):
        with pytest.raises(G.GdbError, match=bad):
            G.create_feature_class(gdb, **kwargs)
    assert G.clean_name("1st patrol-route!") == "f_1st_patrol_route"
    with pytest.raises(G.GdbError, match="only in file geodatabases"):
        G.create_dataset(gdb.replace("ops.gdb", "x.gpkg"), "A")


def test_add_delete_field_rename_delete(gdb):
    G.create_feature_class(gdb, "notes", "point", fields=[{"name": "name"}])
    G.add_field(gdb, "notes", {"name": "priority", "type": "int"})
    assert [f["name"] for f in G.layer_info(gdb, "notes")["fields"]] == ["name", "priority"]
    with pytest.raises(G.GdbError, match="already exists"):
        G.add_field(gdb, "notes", {"name": "priority"})
    G.delete_field(gdb, "notes", "priority")
    assert G.rename_layer(gdb, "notes", "field notes") == "field_notes"
    assert [r["name"] for r in G.contents(gdb)["layers"]] == ["field_notes"]
    G.create_dataset(gdb, "Hydro")
    G.create_feature_class(gdb, "wells", "point", dataset="Hydro")
    assert G.delete_dataset(gdb, "Hydro") == 1
    G.delete_layer(gdb, "field_notes")
    assert G.contents(gdb) == {"datasets": {}, "layers": [], "domains": {}}
    with pytest.raises(G.GdbError):
        G.delete_layer(gdb, "nope")


def test_draw_into_a_feature_class_and_read_it_back(qgis_app, gdb, tmp_path):
    from badr.core import exchange
    from badr.core.workbench import Workbench
    wb = Workbench(); wb.open_operation(None)
    G.create_dataset(gdb, "Transport", 32611)                          # projected: tests the reprojection on insert
    G.create_feature_class(gdb, "routes", "line", dataset="Transport", fields=[{"name": "name"}, {"name": "lanes", "type": "int"}])
    G.create_feature_class(gdb, "wells", "point", fields=[{"name": "name"}, {"name": "notes"}])
    routes, wells = G.open_layer(gdb, "routes"), G.open_layer(gdb, "wells")
    wb.add_layer(routes, "Data"); wb.add_layer(wells, "Data")
    assert set(exchange.editable_layers(wb)) == {routes, wells}
    exchange.add_user_feature(wb, wells, [(LAT, LON)], "Well 1", "seasonal")
    exchange.add_user_feature(wb, routes, [(LAT, LON), (LAT + 0.01, LON)], "RTE A", attrs={"lanes": 2})
    del routes, wells
    wb.project.clear()
    back = QgsVectorLayer(G.uri(gdb, "routes"), "r", "ogr")
    f = next(back.getFeatures())
    assert (f["name"], f["lanes"]) == ("RTE A", 2)
    assert 1100 < f.geometry().length() < 1125                          # metres: stored in the dataset's UTM zone
    w = next(QgsVectorLayer(G.uri(gdb, "wells"), "w", "ogr").getFeatures())
    assert w["notes"] == "seasonal" and abs(w.geometry().asPoint().y() - LAT) < 1e-6
    assert {r["name"]: r["count"] for r in G.contents(gdb)["datasets"]["Transport"]} == {"routes": 1}


def test_import_a_map_layer_into_a_dataset(qgis_app, gdb, tmp_path):
    from badr.core.workbench import Workbench
    wb = Workbench(); wb.open_operation(None)
    wb.operation.add_point("units", LAT, LON, {"sidc": "10031000151211000000", "name": "A COY"})
    G.create_dataset(gdb, "Picture", 4326)
    made = G.import_layer(wb.operation.layer("units"), gdb, None, "Picture")
    assert made == "Units_Equipment"
    rows = G.contents(gdb)["datasets"]["Picture"]
    assert rows[0]["name"] == made and rows[0]["count"] == 1
    f = next(QgsVectorLayer(G.uri(gdb, made), "u", "ogr").getFeatures())
    assert f["sidc"] == "10031000151211000000"
    with pytest.raises(G.GdbError, match="already exists"):
        G.import_layer(wb.operation.layer("units"), gdb, made)


# ---- the Catalog panel -------------------------------------------------------------
@pytest.fixture()
def win(qgis_app, tmp_path):
    from badr.core.settings import Settings
    from badr.ui.main_window import MainWindow
    Settings.set("catalog/connections", "[]")
    w = MainWindow(str(tmp_path / "op"))
    w.show()
    w.wb.zoom_to(LAT, LON, 4000)
    qgis_app.processEvents()
    yield w
    w.close()
    Settings.set("catalog/connections", "[]")


def labels(tree):
    out = []

    def walk(it, depth):
        out.append("  " * depth + it.text(0))
        for i in range(it.childCount()):
            walk(it.child(i), depth + 1)
    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i), 0)
    return out


def test_catalog_tree_and_add_to_map(win, qgis_app):
    from badr.ui.panels import MODES
    assert [m[0] for m in MODES][:2] == ["CONTENTS", "CATALOG"]
    cat = win.panels["CATALOG"]
    assert "No geodatabase yet" in labels(cat.tree)[0] and not cat.b_class.isEnabled()
    path = cat.create_database(os.path.join(cat.data_folder(), "ops.gdb"))
    assert cat.b_class.isEnabled() and cat.b_dataset.isEnabled()
    G.create_dataset(path, "Transport", 4326)
    G.create_feature_class(path, "routes", "line", dataset="Transport", fields=[{"name": "name"}])
    G.create_feature_class(path, "wells", "point", fields=[{"name": "name"}, {"name": "notes"}])
    G.create_domain(path, "Surface", {"P": "Paved"})
    cat.refresh()
    tree = labels(cat.tree)
    assert tree[0] == "ops.gdb" and any("Transport" in t and "feature dataset" in t for t in tree)
    assert any("routes" in t and "(0)" in t for t in tree) and any("wells" in t for t in tree)
    assert "  Domains" in tree and any("Surface  (1 values)" in t for t in tree)

    cat._select(("class", path, "wells"))
    assert cat.b_add.isEnabled() and cat.b_props.isEnabled()
    cat.add_to_map(cat.tree.currentItem())
    layer = win._catalog_layers(path, "wells")[0]
    assert layer in win.wb.project.mapLayers().values()
    cat.add_to_map(cat.tree.currentItem())
    assert len(win._catalog_layers(path, "wells")) == 1               # not added twice

    draw = win.panels["DRAW"]
    assert any("wells" in draw.layer.itemText(i) and "geodatabase" in draw.layer.itemText(i)
               for i in range(draw.layer.count()))
    from badr.core import exchange
    exchange.add_user_feature(win.wb, layer, [(LAT, LON)], "Well 1")
    cat.refresh()
    assert any("wells" in t and "(1)" in t for t in labels(cat.tree))
    tr_point = __import__("qgis.core", fromlist=["QgsPointXY"]).QgsPointXY
    from qgis.core import QgsCoordinateTransform
    mp = QgsCoordinateTransform(win.wb.wgs84, win.canvas.mapSettings().destinationCrs(), win.wb.project).transform(tr_point(LON, LAT))
    win._select_at(mp)
    assert win._selection and win._selection[0][0] is layer and "Well 1" in win.inspector.title.text()


def test_catalog_layers_reopen_with_the_operation_and_can_be_deleted(win, qgis_app, monkeypatch):
    from qgis.PyQt.QtWidgets import QMessageBox
    cat = win.panels["CATALOG"]
    path = cat.create_database(os.path.join(cat.data_folder(), "ops.gdb"))
    G.create_feature_class(path, "wells", "point", fields=[{"name": "name"}])
    win.add_catalog_layer(path, "wells")
    folder = win.wb.operation.folder
    win._open(folder)
    assert len(win._catalog_layers(path, "wells")) == 1               # back on the map after reopening
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    cat.refresh()
    cat._select(("class", path, "wells"))
    cat.delete()
    assert win._catalog_layers(path, "wells") == [] and G.contents(path)["layers"] == []
    assert win.wb.operation.data_layers == []


def test_feature_class_dialog_builds_the_class(win, qgis_app):
    from badr.ui.catalog import FeatureClassDialog, PropertiesDialog
    cat = win.panels["CATALOG"]
    path = cat.create_database(os.path.join(cat.data_folder(), "ops.gdb"))
    G.create_dataset(path, "Hydro", 32611)
    G.create_domain(path, "Kind", {"W": "Well", "S": "Spring"})
    dlg = FeatureClassDialog(win.wb, path, "Hydro", False, win)
    assert dlg.dataset.currentData() == "Hydro" and not dlg.crs.isEnabled() and "32611" in dlg.note.text()
    dlg.name.setText("water points")
    dlg.fields.add_row("kind", "Text", "Source type", "Kind")
    assert dlg.create() == "water_points"
    info = G.layer_info(path, "water_points")
    assert info["dataset"] == "Hydro" and info["epsg"] == 32611
    assert [f["name"] for f in info["fields"]] == ["name", "notes", "kind"] and info["fields"][2]["domain"] == "Kind"
    props = PropertiesDialog(path, "water_points", win)
    assert props.table.rowCount() == 3 and "Hydro" in props.summary.text()
    free = FeatureClassDialog(win.wb, path, None, False, win)
    free.crs.setCurrentText("EPSG:32642")
    assert free.epsg() == 32642 and free.crs.isEnabled()


def test_assistant_geodatabase_tool(win):
    def call(**args):
        text, err = win.ai_tools.call("geodatabase", args)
        assert not err, text
        return text
    assert "Created" in call(action="create", name="recon")
    call(action="dataset", name="Terrain", epsg=4326)
    call(action="feature_class", name="key_terrain", geometry="polygon", dataset="Terrain", fields=["grade:int", "notes"])
    call(action="domain", name="Grade", values={"1": "Decisive", "2": "Key"})
    listing = call(action="list")
    assert "key_terrain" in listing and "Terrain" in listing and "Grade" in listing
    assert "added to the map" in call(action="add_to_map", name="key_terrain")
    text, err = win.ai_tools.call("geodatabase", {"action": "feature_class", "name": "key_terrain"})
    assert err and "already exists" in text
