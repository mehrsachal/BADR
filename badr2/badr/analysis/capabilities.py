"""The whole of QGIS, indexed for a planner and for the AI.

QGIS is open source, so nothing needs reverse-engineering: BADR links the QGIS
engine and gets every provider and every processing algorithm (native C++,
GDAL, and GRASS/SAGA when installed).  The problem is the opposite one - about
a thousand tools presented in GIS vocabulary.  This module:

* introspects the live processing registry (so it always matches the installed
  QGIS version),
* files each algorithm under a military planning workflow,
* gives the UI and the AI one search function over all of them, and
* runs any of them by id with plain JSON parameters.
"""
import re

from qgis.core import (
    QgsApplication, QgsMapLayer, QgsProcessingContext, QgsProcessingFeedback,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorDestination, QgsProject,
)

# workflow -> keywords matched against algorithm id/name/group/tags
WORKFLOWS = [
    ("Terrain & visibility", ["viewshed", "slope", "aspect", "hillshade", "relief", "ruggedness", "roughness",
                              "contour", "elevation", "dem", "terrain", "tpi", "tri", "hypsometric", "drape",
                              "profile", "line of sight"]),
    ("Movement & routes", ["shortest path", "service area", "network", "route", "least cost", "path",
                           "isochrone", "travel"]),
    ("Pattern & hotspot analysis", ["heatmap", "kernel density", "cluster", "dbscan", "k-means", "kmeans",
                                    "nearest neighbour", "nearest neighbor", "hub", "count points", "density",
                                    "statistics", "mean coordinate", "standard distance", "hot spot"]),
    ("Imagery & change detection", ["raster calculator", "band", "pansharpen", "ndvi", "classif", "reclass",
                                    "zonal", "threshold", "sieve", "polygonize", "resample", "warp", "merge",
                                    "mosaic", "clip raster", "raster", "color", "rgb", "georeferenc", "cell stat"]),
    ("Areas, buffers & overlays", ["buffer", "intersect", "union", "difference", "clip", "dissolve", "overlap",
                                   "within", "contains", "extract by location", "join attributes by location",
                                   "select by location", "split", "sector", "wedge", "ring"]),
    ("Grids & references", ["grid", "graticule", "index", "atlas", "tile", "mgrs", "reference"]),
    ("Measurement & geometry", ["distance", "area", "length", "centroid", "bearing", "azimuth", "geodesic",
                                "densify", "simplify", "smooth", "bounding", "convex hull", "voronoi",
                                "delaunay", "point along", "offset", "rotate", "translate", "geometry"]),
    ("Data management", ["reproject", "convert", "export", "import", "package", "attribute", "field", "refactor",
                         "table", "database", "download", "metadata", "rename", "layer", "save", "load", "sql",
                         "append", "duplicate", "fix", "valid"]),
]
OTHER = "Other GIS tools"


def _classify(text):
    for name, words in WORKFLOWS:
        for w in words:
            if w in text:
                return name
    return OTHER


class CapabilityIndex:
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.items = []
        self.refresh()

    def refresh(self):
        self.items = []
        for alg in QgsApplication.processingRegistry().algorithms():
            try:
                tags = " ".join(alg.tags())
            except Exception:
                tags = ""
            text = f"{alg.id()} {alg.displayName()} {alg.group()} {tags}".lower()
            self.items.append({
                "id": alg.id(), "name": alg.displayName(), "group": alg.group(),
                "provider": alg.provider().name() if alg.provider() else "",
                "workflow": _classify(f"{alg.displayName()} {alg.group()} {tags}".lower()),
                "_text": text, "_name": alg.displayName().lower(),
            })
        self.items.sort(key=lambda i: (i["workflow"], i["name"]))

    def __len__(self):
        return len(self.items)

    def by_workflow(self):
        out = {}
        for it in self.items:
            out.setdefault(it["workflow"], []).append(it)
        return out

    def providers(self):
        out = {}
        for it in self.items:
            out[it["provider"]] = out.get(it["provider"], 0) + 1
        return out

    def search(self, query, limit=10):
        tokens = re.findall(r"[a-z0-9]+", str(query).lower())
        if not tokens:
            return []
        scored = []
        for it in self.items:
            s = 0.0
            for t in tokens:
                if t in it["_name"].split():
                    s += 3
                elif t in it["_name"]:
                    s += 2
                elif t in it["_text"]:
                    s += 1
            if s:
                if it["id"].startswith("native:"):
                    s += 0.3
                scored.append((s - len(it["_name"]) / 200.0, it))
        scored.sort(key=lambda x: -x[0])
        return [{k: v for k, v in it.items() if not k.startswith("_")} for _s, it in scored[:limit]]

    # ---- description & execution ---------------------------------------
    @staticmethod
    def describe(alg_id):
        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        if alg is None:
            raise KeyError(f"unknown algorithm {alg_id!r}")
        params = []
        for p in alg.parameterDefinitions():
            if p.flags() & QgsProcessingParameterDefinition.FlagHidden:
                continue
            d = {"name": p.name(), "type": p.type(), "description": p.description(),
                 "optional": bool(p.flags() & QgsProcessingParameterDefinition.FlagOptional)}
            try:
                default = p.defaultValue()
                if default is not None and not hasattr(default, "sink"):
                    d["default"] = default if isinstance(default, (int, float, str, bool, list)) else str(default)
            except Exception:
                pass
            if hasattr(p, "options"):
                try:
                    d["options"] = list(p.options())
                except Exception:
                    pass
            params.append(d)
        help_text = (alg.shortHelpString() or alg.shortDescription() or "").strip()
        help_text = re.sub(r"<[^>]+>", "", help_text)
        return {"id": alg.id(), "name": alg.displayName(), "group": alg.group(),
                "help": help_text[:900], "parameters": params,
                "outputs": [{"name": o.name(), "type": o.type()} for o in alg.outputDefinitions()]}

    @staticmethod
    def run(alg_id, params, workbench=None, feedback=None):
        """Run any algorithm.  Layer parameters may be given by layer name.

        Destination parameters left out are written to the operation's analysis
        folder and loaded onto the map.  Returns {outputs, loaded_layers}.
        """
        import processing
        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        if alg is None:
            raise KeyError(f"unknown algorithm {alg_id!r}")
        project = QgsProject.instance()
        params = dict(params or {})

        def resolve(v):
            if isinstance(v, str) and workbench is not None:
                lyr = workbench.find_layer(v)
                if lyr is not None:
                    return lyr
            return v
        for p in alg.parameterDefinitions():
            name = p.name()
            if p.type() in ("source", "vector", "raster", "layer", "mesh", "pointcloud") and name in params:
                params[name] = resolve(params[name])
            elif p.type() == "multilayer" and isinstance(params.get(name), list):
                params[name] = [resolve(v) for v in params[name]]
            elif isinstance(p, (QgsProcessingParameterFeatureSink, QgsProcessingParameterVectorDestination,
                                QgsProcessingParameterRasterDestination)) and name not in params:
                if p.flags() & QgsProcessingParameterDefinition.FlagOptional and p.defaultValue() is None:
                    continue
                ext = ".tif" if isinstance(p, QgsProcessingParameterRasterDestination) else ".gpkg"
                stem = re.sub(r"[^a-z0-9]+", "_", alg.displayName().lower()).strip("_")
                params[name] = workbench.out_path(stem, ext) if workbench else "TEMPORARY_OUTPUT"
        context = QgsProcessingContext()
        context.setProject(project)
        result = processing.run(alg_id, params, context=context, feedback=feedback or QgsProcessingFeedback())
        outputs, loaded = {}, []
        from qgis.core import QgsRasterLayer, QgsVectorLayer
        for k, v in result.items():
            if isinstance(v, QgsMapLayer):
                if workbench is not None:
                    v.setName(alg.displayName())
                    workbench.add_layer(v, "Analysis")
                    loaded.append(v.name())
                outputs[k] = v.name()
            elif isinstance(v, str) and workbench is not None and v.lower().endswith((".tif", ".gpkg", ".shp", ".geojson")):
                lyr = QgsRasterLayer(v, alg.displayName()) if v.lower().endswith(".tif") \
                    else QgsVectorLayer(v, alg.displayName(), "ogr")
                if lyr.isValid():
                    workbench.add_layer(lyr, "Analysis")
                    loaded.append(lyr.name())
                outputs[k] = v
            elif isinstance(v, (int, float, str, bool, list, dict)) or v is None:
                outputs[k] = v
            else:
                outputs[k] = str(v)
        return {"outputs": outputs, "loaded_layers": loaded}
