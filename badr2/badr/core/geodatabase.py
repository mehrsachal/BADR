"""Geodatabase management, ArcGIS-style.

Works on real Esri **file geodatabases** (``.gdb``, read and written through
GDAL's OpenFileGDB driver, so ArcGIS Pro opens them directly) and on
GeoPackages (``.gpkg``).  Concepts map one to one:

    geodatabase      a .gdb folder (or .gpkg file)
    feature dataset  a named group of feature classes sharing one coordinate system (.gdb only)
    feature class    a table of points, lines or polygons
    table            rows without geometry
    domain           a list of allowed coded values, attachable to fields

GDAL can only create a feature dataset together with its first feature class,
so an empty one is remembered in a small side file (``<name>.gdb.badr.json``,
next to - never inside - the geodatabase) until something is put in it.

Every function opens the database, does one thing and closes it again, so no
write handle is ever left open.
"""
import json
import os
import re

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

GEOMETRY = {"point": ogr.wkbPoint, "line": ogr.wkbMultiLineString, "polygon": ogr.wkbMultiPolygon,
            "table": ogr.wkbNone}
FIELD_TYPES = {                       # label -> (OGR type, subtype)
    "Text": (ogr.OFTString, ogr.OFSTNone),
    "Whole number": (ogr.OFTInteger, ogr.OFSTNone),
    "Decimal": (ogr.OFTReal, ogr.OFSTNone),
    "Date": (ogr.OFTDate, ogr.OFSTNone),
    "Date and time": (ogr.OFTDateTime, ogr.OFSTNone),
}
_TYPE_ALIASES = {"text": "Text", "string": "Text", "str": "Text", "int": "Whole number", "integer": "Whole number",
                 "long": "Whole number", "number": "Decimal", "double": "Decimal", "float": "Decimal",
                 "decimal": "Decimal", "date": "Date", "datetime": "Date and time", "time": "Date and time"}
_OGR_LABEL = {ogr.OFTString: "Text", ogr.OFTInteger: "Whole number", ogr.OFTInteger64: "Whole number",
              ogr.OFTReal: "Decimal", ogr.OFTDate: "Date", ogr.OFTDateTime: "Date and time"}
RESERVED = {"objectid", "shape", "shape_length", "shape_area", "fid", "oid"}


class GdbError(ValueError):
    pass


def kind(path):
    p = str(path).lower().rstrip("/\\")
    if p.endswith(".gdb"):
        return "gdb"
    if p.endswith(".gpkg"):
        return "gpkg"
    return None


def clean_name(name, what="name"):
    """Geodatabase object names: start with a letter; letters, digits, underscore; <= 64 chars."""
    n = re.sub(r"[^A-Za-z0-9_]+", "_", str(name or "").strip()).strip("_")
    if not n:
        raise GdbError(f"the {what} is empty")
    if not n[0].isalpha():
        n = "f_" + n
    return n[:64]


def field_type(label):
    key = label if label in FIELD_TYPES else _TYPE_ALIASES.get(str(label).strip().lower())
    if key not in FIELD_TYPES:
        raise GdbError(f"unknown field type {label!r}; use one of {list(FIELD_TYPES)}")
    return key


def _open(path, update=False):
    if not os.path.exists(path):
        raise GdbError(f"geodatabase not found: {path}")
    flags = gdal.OF_VECTOR | (gdal.OF_UPDATE if update else 0)
    # Name the driver explicitly: GDAL's auto-detection does not recognise a
    # file geodatabase that has no feature classes yet.
    driver = {"gdb": ["OpenFileGDB"], "gpkg": ["GPKG"]}.get(kind(path))
    try:
        return gdal.OpenEx(path, flags, allowed_drivers=driver) if driver else gdal.OpenEx(path, flags)
    except RuntimeError as exc:
        if driver and not update:
            try:          # an empty database only opens in update mode
                return gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_UPDATE, allowed_drivers=driver)
            except RuntimeError:
                pass
        raise GdbError(f"could not open {os.path.basename(path)}: {exc}") from exc


def _side(path):
    return path.rstrip("/\\") + ".badr.json"


def _side_read(path):
    try:
        with open(_side(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return dict(data.get("empty_datasets", {})), list(data.get("removed_datasets", []))


def _side_write(path, pending, removed):
    if pending or removed:
        with open(_side(path), "w", encoding="utf-8") as fh:
            json.dump({"empty_datasets": pending, "removed_datasets": removed}, fh)
    elif os.path.exists(_side(path)):
        os.remove(_side(path))


def _pending(path):
    return _side_read(path)[0]


def _save_pending(path, pending):
    _side_write(path, pending, _side_read(path)[1])


def _removed(path):
    """Feature datasets the user deleted.  GDAL cannot remove the (now empty)
    dataset entry itself, so it is hidden until something is created in it again."""
    return _side_read(path)[1]


def _set_removed(path, name, removed):
    pending, names = _side_read(path)
    names = [n for n in names if n != name]
    if removed:
        names.append(name)
    _side_write(path, pending, names)


# ---------------------------------------------------------------------------
def create(path):
    """Create an empty file geodatabase (.gdb) or GeoPackage (.gpkg)."""
    k = kind(path)
    if k is None:
        path, k = path + ".gdb", "gdb"
    if os.path.exists(path):
        raise GdbError(f"{os.path.basename(path)} already exists")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    ds = gdal.GetDriverByName("OpenFileGDB" if k == "gdb" else "GPKG").Create(path, 0, 0, 0, gdal.GDT_Unknown)
    if ds is None:
        raise GdbError(f"could not create {path}")
    ds = None
    return path


def _layer_row(lyr, dataset=None):
    gt = ogr.GT_Flatten(lyr.GetGeomType())
    geom = {ogr.wkbPoint: "point", ogr.wkbMultiPoint: "point", ogr.wkbLineString: "line",
            ogr.wkbMultiLineString: "line", ogr.wkbPolygon: "polygon", ogr.wkbMultiPolygon: "polygon",
            ogr.wkbNone: "table"}.get(gt, "other")
    srs = lyr.GetSpatialRef()
    epsg = None
    if srs is not None:
        try:
            srs.AutoIdentifyEPSG()
        except RuntimeError:
            pass
        code = srs.GetAuthorityCode(None)
        epsg = int(code) if code else None
    return {"name": lyr.GetName(), "geometry": geom, "count": lyr.GetFeatureCount(), "epsg": epsg,
            "dataset": dataset}


def contents(path):
    """-> {"datasets": {name: [layer, ...]}, "layers": [...], "domains": {name: {code: text}}}"""
    ds = _open(path)
    out = {"datasets": {}, "layers": [], "domains": {}}
    seen = set()
    root = None
    try:
        root = ds.GetRootGroup()
    except (RuntimeError, AttributeError):
        root = None
    if root is not None:
        for gname in root.GetGroupNames() or []:
            group = root.OpenGroup(gname)
            rows = []
            for lname in group.GetVectorLayerNames() or []:
                lyr = ds.GetLayerByName(lname)
                if lyr is not None:
                    rows.append(_layer_row(lyr, gname))
                    seen.add(lname)
            out["datasets"][gname] = rows
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayer(i)
        if lyr.GetName() not in seen:
            out["layers"].append(_layer_row(lyr))
    try:
        for dname in ds.GetFieldDomainNames() or []:
            dom = ds.GetFieldDomain(dname)
            if dom is not None and dom.GetDomainType() == ogr.OFDT_CODED:
                out["domains"][dname] = dict(dom.GetEnumeration() or {})
    except (RuntimeError, AttributeError):
        pass
    del root
    ds = None
    for name, epsg in _pending(path).items():
        out["datasets"].setdefault(name, [])
    for name in _removed(path):
        if name in out["datasets"] and not out["datasets"][name]:
            del out["datasets"][name]
    out["layers"].sort(key=lambda r: r["name"].lower())
    return out


def dataset_epsg(path, dataset):
    """Coordinate system shared by a feature dataset (None if unknown)."""
    c = contents(path)
    rows = c["datasets"].get(dataset)
    if rows:
        return rows[0]["epsg"]
    return _pending(path).get(dataset)


def create_dataset(path, name, epsg=4326):
    if kind(path) != "gdb":
        raise GdbError("feature datasets exist only in file geodatabases (.gdb)")
    name = clean_name(name, "feature dataset name")
    if name in contents(path)["datasets"]:
        raise GdbError(f"feature dataset '{name}' already exists")
    pending = _pending(path)
    pending[name] = int(epsg)
    _save_pending(path, pending)
    _set_removed(path, name, False)
    return name


def _field_defn(spec, domains):
    name = clean_name(spec.get("name"), "field name")
    if name.lower() in RESERVED:
        raise GdbError(f"'{name}' is a reserved field name")
    otype, sub = FIELD_TYPES[field_type(spec.get("type", "Text"))]
    fd = ogr.FieldDefn(name, otype)
    fd.SetSubType(sub)
    if otype == ogr.OFTString:
        fd.SetWidth(int(spec.get("length") or 255))
    if spec.get("alias"):
        fd.SetAlternativeName(str(spec["alias"]))
    dom = spec.get("domain")
    if dom:
        if dom not in domains:
            raise GdbError(f"domain '{dom}' does not exist in this geodatabase")
        fd.SetDomainName(dom)
    return fd


def create_feature_class(path, name, geometry="point", epsg=4326, dataset=None, fields=()):
    """Create a feature class (or a table when geometry == 'table'). Returns its name."""
    name = clean_name(name, "feature class name")
    geometry = str(geometry).lower()
    if geometry not in GEOMETRY:
        raise GdbError("geometry must be point, line, polygon or table")
    c = contents(path)
    existing = {r["name"].lower() for rows in c["datasets"].values() for r in rows} | \
               {r["name"].lower() for r in c["layers"]}
    if name.lower() in existing:
        raise GdbError(f"'{name}' already exists in this geodatabase")
    options = []
    if dataset:
        if kind(path) != "gdb":
            raise GdbError("feature datasets exist only in file geodatabases (.gdb)")
        if geometry == "table":
            raise GdbError("tables cannot live inside a feature dataset")
        dataset = clean_name(dataset, "feature dataset name")
        shared = dataset_epsg(path, dataset)
        if shared:
            epsg = shared                          # every class in a dataset shares its coordinate system
        options.append(f"FEATURE_DATASET={dataset}")
    srs = None
    if geometry != "table":
        srs = osr.SpatialReference()
        try:
            srs.ImportFromEPSG(int(epsg))
        except RuntimeError as exc:
            raise GdbError(f"unknown coordinate system EPSG:{epsg}") from exc
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    defs = [_field_defn(f, c["domains"]) for f in fields]
    names = [d.GetName().lower() for d in defs]
    if len(names) != len(set(names)):
        raise GdbError("two fields have the same name")
    ds = _open(path, update=True)
    try:
        lyr = ds.CreateLayer(name, srs, GEOMETRY[geometry], options=options)
        for d in defs:
            lyr.CreateField(d)
    except RuntimeError as exc:
        raise GdbError(f"could not create '{name}': {exc}") from exc
    finally:
        lyr = None
        ds = None
    if dataset:
        pending = _pending(path)
        if pending.pop(dataset, None) is not None:
            _save_pending(path, pending)
        if dataset in _removed(path):
            _set_removed(path, dataset, False)
    return name


def create_domain(path, name, values, description="", field_type_label="Text"):
    """Coded-value domain: {code: description}."""
    name = clean_name(name, "domain name")
    if not values:
        raise GdbError("a domain needs at least one coded value")
    otype, sub = FIELD_TYPES[field_type(field_type_label)]
    ds = _open(path, update=True)
    try:
        if name in (ds.GetFieldDomainNames() or []):
            raise GdbError(f"domain '{name}' already exists")
        dom = ogr.CreateCodedFieldDomain(name, description or name, otype, sub,
                                         {str(k): str(v) for k, v in values.items()})
        if not ds.AddFieldDomain(dom):
            raise GdbError("this database format does not support domains")
    except RuntimeError as exc:
        raise GdbError(f"could not create domain: {exc}") from exc
    finally:
        ds = None
    return name


def layer_info(path, layer):
    ds = _open(path)
    lyr = ds.GetLayerByName(layer)
    if lyr is None:
        ds = None
        raise GdbError(f"'{layer}' not found")
    row = _layer_row(lyr)
    defn = lyr.GetLayerDefn()
    row["fields"] = []
    for i in range(defn.GetFieldCount()):
        fd = defn.GetFieldDefn(i)
        row["fields"].append({"name": fd.GetName(), "type": _OGR_LABEL.get(fd.GetType(), fd.GetTypeName()),
                              "alias": fd.GetAlternativeName() or "", "domain": fd.GetDomainName() or "",
                              "length": fd.GetWidth()})
    srs = lyr.GetSpatialRef()
    row["crs"] = srs.GetName() if srs is not None else ""
    lyr = None
    ds = None
    for dname, rows in contents(path)["datasets"].items():
        if any(r["name"] == layer for r in rows):
            row["dataset"] = dname
    return row


def _with_layer(path, layer, fn):
    ds = _open(path, update=True)
    lyr = ds.GetLayerByName(layer)
    if lyr is None:
        ds = None
        raise GdbError(f"'{layer}' not found")
    try:
        return fn(ds, lyr)
    except RuntimeError as exc:
        raise GdbError(str(exc)) from exc
    finally:
        lyr = None
        ds = None


def add_field(path, layer, spec):
    doms = contents(path)["domains"]
    fd = _field_defn(spec, doms)

    def go(ds, lyr):
        if lyr.GetLayerDefn().GetFieldIndex(fd.GetName()) >= 0:
            raise GdbError(f"field '{fd.GetName()}' already exists")
        lyr.CreateField(fd)
        return fd.GetName()
    return _with_layer(path, layer, go)


def delete_field(path, layer, field):
    def go(ds, lyr):
        idx = lyr.GetLayerDefn().GetFieldIndex(field)
        if idx < 0:
            raise GdbError(f"field '{field}' not found")
        lyr.DeleteField(idx)
        return True
    return _with_layer(path, layer, go)


def rename_layer(path, layer, new_name):
    new_name = clean_name(new_name)

    def go(ds, lyr):
        if not lyr.TestCapability(ogr.OLCRename):
            raise GdbError("this database format cannot rename")
        lyr.Rename(new_name)
        return new_name
    return _with_layer(path, layer, go)


def delete_layer(path, layer):
    ds = _open(path, update=True)
    try:
        for i in range(ds.GetLayerCount()):
            if ds.GetLayer(i).GetName() == layer:
                ds.DeleteLayer(i)
                return True
        raise GdbError(f"'{layer}' not found")
    except RuntimeError as exc:
        raise GdbError(str(exc)) from exc
    finally:
        ds = None


def delete_dataset(path, dataset):
    """Delete a feature dataset and every feature class in it."""
    rows = contents(path)["datasets"].get(dataset)
    if rows is None:
        raise GdbError(f"feature dataset '{dataset}' not found")
    for r in rows:
        delete_layer(path, r["name"])
    pending = _pending(path)
    if pending.pop(dataset, None) is not None:
        _save_pending(path, pending)
    _set_removed(path, dataset, True)
    return len(rows)


# ---------------------------------------------------------------------------
# QGIS side: load, import
# ---------------------------------------------------------------------------
def uri(path, layer):
    return f"{path}|layername={layer}"


def open_layer(path, layer, title=None):
    from qgis.core import QgsVectorLayer
    lyr = QgsVectorLayer(uri(path, layer), title or layer, "ogr")
    if not lyr.isValid():
        raise GdbError(f"could not load '{layer}'")
    lyr.setCustomProperty("badr/editable", True)
    lyr.setCustomProperty("badr/gdb", path)
    lyr.setCustomProperty("badr/gdb_layer", layer)
    return lyr


def import_layer(qgs_layer, path, name=None, dataset=None, selected_only=False):
    """Copy a map layer (any source) into the geodatabase as a new feature class."""
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsVectorFileWriter,
    )
    name = clean_name(name or qgs_layer.name(), "feature class name")
    c = contents(path)
    existing = {r["name"].lower() for rows in c["datasets"].values() for r in rows} | \
               {r["name"].lower() for r in c["layers"]}
    if name.lower() in existing:
        raise GdbError(f"'{name}' already exists in this geodatabase")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "OpenFileGDB" if kind(path) == "gdb" else "GPKG"
    opts.layerName = name
    opts.fileEncoding = "UTF-8"
    opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    opts.onlySelectedFeatures = bool(selected_only and qgs_layer.selectedFeatureCount())
    layer_opts = []
    if dataset:
        if kind(path) != "gdb":
            raise GdbError("feature datasets exist only in file geodatabases (.gdb)")
        dataset = clean_name(dataset, "feature dataset name")
        layer_opts.append(f"FEATURE_DATASET={dataset}")
        shared = dataset_epsg(path, dataset)
        if shared and qgs_layer.crs().authid() != f"EPSG:{shared}":
            opts.ct = QgsCoordinateTransform(qgs_layer.crs(), QgsCoordinateReferenceSystem(f"EPSG:{shared}"),
                                             QgsProject.instance())
    opts.layerOptions = layer_opts
    res = QgsVectorFileWriter.writeAsVectorFormatV3(qgs_layer, path, QgsProject.instance().transformContext(), opts)
    if res[0] != QgsVectorFileWriter.NoError:
        raise GdbError(f"import failed: {res[1]}")
    if dataset:
        pending = _pending(path)
        if pending.pop(dataset, None) is not None:
            _save_pending(path, pending)
        if dataset in _removed(path):
            _set_removed(path, dataset, False)
    return name
