"""The agent's tools: every capability of the application as a structured action.

The AI never clicks through a UI and rarely writes code; it calls these.  The
same functions back the buttons in the panels, so anything a planner can do by
hand the assistant can do by request, and vice versa.

Anything the AI adds to the picture is tagged ``source='ai'`` and held as
*pending review* until a human accepts it (see ``Operation.resolve``).
"""
import json
import re

from qgis.core import QgsGeometry, QgsPointXY

from ..analysis import pattern, terrain
from ..analysis.capabilities import CapabilityIndex
from ..core import geodesy
from ..core.mgrs import parse_coordinate, to_mgrs
from ..core.settings import Settings
from ..symbology import sidc as S
from ..symbology.palette import GRAPHIC_KINDS, PALETTE


class ToolError(Exception):
    """A problem the model can fix by calling the tool differently."""


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


LOC = {"type": "string", "description": "A location: MGRS ('42SYD 3315 6670'), 'lat, lon' in decimal degrees, "
                                        "the uid or name of a feature already on the map, or 'view' for the "
                                        "centre of the current map view."}


class Toolbox:
    def __init__(self, workbench, devices=None, confirm_code=None, source="ai"):
        self.wb = workbench
        self.devices = devices
        self.confirm_code = confirm_code        # callable(code, explanation) -> bool
        self.source = source
        self._tools = {}
        self._register()

    # ------------------------------------------------------------------
    def _add(self, name, description, params, fn, required=()):
        self._tools[name] = {"name": name, "description": description,
                             "parameters": _schema(params, required), "fn": fn}

    def specs(self):
        return [{k: v for k, v in t.items() if k != "fn"} for t in self._tools.values()]

    def names(self):
        return list(self._tools)

    def call(self, name, args):
        """Run a tool. Returns (text_for_model, is_error)."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool {name!r}. Available: {', '.join(self._tools)}", True
        if "_invalid_json" in (args or {}):
            return "Tool arguments were not valid JSON; send them again.", True
        try:
            out = tool["fn"](**(args or {}))
        except (ToolError, ValueError, KeyError, terrain.NoDem) as exc:
            return f"{exc}", True
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}", True
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}", True
        self.wb.refresh()
        text = out if isinstance(out, str) else json.dumps(out, default=str, ensure_ascii=False)
        if len(text) > 6000:
            text = text[:6000] + " ...[truncated]"
        return text, False

    # ---- location resolution -------------------------------------------
    def locate(self, text):
        """-> (lat, lon)."""
        if isinstance(text, (list, tuple)) and len(text) == 2:
            return float(text[0]), float(text[1])
        t = str(text or "").strip()
        if not t:
            raise ToolError("location is empty")
        if t.lower() in ("view", "here", "center", "centre", "map", "screen"):
            c = self.wb.center_wgs84()
            if not c:
                raise ToolError("the map view has no extent yet; give coordinates")
            return c
        try:
            return parse_coordinate(t)
        except ValueError:
            pass
        op = self.wb.operation
        key, feat = op.find_any(t)
        if feat is None:
            low = t.lower()
            for k, layer in op.layers.items():
                for fld in ("name", "label"):
                    if layer.fields().indexOf(fld) < 0:
                        continue
                    for f in layer.getFeatures():
                        if str(f[fld] or "").lower() == low:
                            feat = f
                            break
                    if feat is not None:
                        break
                if feat is not None:
                    break
        if feat is None:
            raise ToolError(f"could not resolve location {t!r}: not MGRS, not 'lat, lon', and no feature has "
                            "that uid or name")
        p = feat.geometry().centroid().asPoint()
        return p.y(), p.x()

    def _bbox(self, area=None, radius_m=None):
        """Optional area spec -> (W,S,E,N). ``area`` is a location; radius defaults to 3 km."""
        if not area:
            ext = self.wb.extent_wgs84()
            if not ext:
                raise ToolError("no map extent; pass an area")
            return ext
        lat, lon = self.locate(area)
        r = float(radius_m or 3000)
        n, _ = geodesy.destination(lat, lon, 0, r)
        _, e = geodesy.destination(lat, lon, 90, r)
        return (lon - (e - lon), lat - (n - lat), e, n)

    def _resolve_symbol(self, symbol, affiliation, echelon=None, status=None):
        symbol = str(symbol or "").strip()
        if S.is_valid(symbol):
            code = symbol
            if affiliation:
                code = S.with_affiliation(code, affiliation)
        else:
            item = None
            low = symbol.lower()
            for items in PALETTE.values():
                for it in items:
                    if "entity" in it and it["label"].lower() == low:
                        item = it
                        break
                if item:
                    break
            if item:
                code = S.build(item["set"], item["entity"], affiliation or item.get("affiliation", "unknown"),
                               echelon=item.get("echelon", "none"), hq=item.get("hq", "none"),
                               mod1=item.get("mod1", "00"), mod2=item.get("mod2", "00"))
            else:
                hits = S.search(symbol, limit=1)
                if not hits:
                    raise ToolError(f"no APP-6 symbol matches {symbol!r}; call find_symbol to browse")
                code = S.build(hits[0]["symbol_set"], hits[0]["entity"], affiliation or "unknown")
        if echelon:
            code = S.with_echelon(code, echelon)
        if status:
            code = S.with_status(code, status)
        return code

    # ------------------------------------------------------------------
    def _register(self):
        add = self._add
        add("get_picture",
            "Read the current common operational picture: units, incidents, control measures, live tracks, "
            "loaded layers, map view and pending-review items. Call this before reasoning about the situation.",
            {}, self.get_picture)
        add("go_to", "Move the map to a location.",
            {"location": LOC, "radius_m": {"type": "number", "description": "half-width of the view, default 3000"}},
            self.go_to, ["location"])
        add("find_symbol",
            "Search the full NATO APP-6D catalog (1,600+ entities: units, equipment, installations, activities, "
            "control measures) for the right symbol. Returns symbol_set + entity codes.",
            {"query": {"type": "string"},
             "symbol_sets": {"type": "array", "items": {"type": "string", "enum": sorted(S.SYMBOL_SETS)}}},
            self.find_symbol, ["query"])
        add("place_symbol",
            "Place a NATO APP-6D symbol (unit, equipment, installation, human-terrain point, control point) on the "
            "map. 'symbol' may be a 20-digit SIDC, a palette label, or a plain description such as 'mechanized "
            "infantry', 'safe house', 'checkpoint'.",
            {"location": LOC, "symbol": {"type": "string"},
             "affiliation": {"type": "string", "enum": sorted(S.AFFILIATIONS)},
             "name": {"type": "string", "description": "unique designation shown beside the symbol"},
             "echelon": {"type": "string", "enum": sorted(set(S.ECHELONS) - {"none"})},
             "higher": {"type": "string", "description": "higher formation"},
             "status": {"type": "string", "enum": ["present", "planned"]},
             "remarks": {"type": "string"}},
            self.place_symbol, ["location", "symbol"])
        add("log_incident",
            "Record an incident / SIGACT (IED, shooting, kidnapping, demonstration ...) for pattern analysis.",
            {"location": LOC, "type": {"type": "string", "description": "e.g. 'IED explosion', 'sniping', 'arson'"},
             "dtg": {"type": "string", "description": "DDHHMMZMONYY or ISO time; default now"},
             "description": {"type": "string"}, "casualties": {"type": "integer"},
             "affiliation": {"type": "string", "enum": sorted(S.AFFILIATIONS)},
             "reliability": {"type": "string", "description": "source reliability A-F"},
             "credibility": {"type": "string", "description": "information credibility 1-6"}},
            self.log_incident, ["location", "type"])
        add("draw_graphic",
            "Draw a tactical graphic / control measure. Lines need 2+ points; areas need 3+ points, or one point "
            "with radius_m for a circular area.",
            {"kind": {"type": "string", "enum": sorted(k for k in GRAPHIC_KINDS
                                                       if k not in ("los_visible", "los_hidden", "grg", "analysis"))},
             "points": {"type": "array", "items": LOC}, "radius_m": {"type": "number"},
             "name": {"type": "string"}, "affiliation": {"type": "string", "enum": ["friend", "hostile", "neutral"]},
             "status": {"type": "string", "enum": ["present", "planned"]}, "remarks": {"type": "string"}},
            self.draw_graphic, ["kind", "points"])
        add("update_feature", "Change or move something on the map, identified by uid.",
            {"uid": {"type": "string"}, "location": LOC, "name": {"type": "string"},
             "affiliation": {"type": "string", "enum": sorted(S.AFFILIATIONS)},
             "status": {"type": "string", "enum": ["present", "planned", "damaged", "destroyed"]},
             "remarks": {"type": "string"}},
            self.update_feature, ["uid"])
        add("delete_feature", "Remove a feature from the map by uid.", {"uid": {"type": "string"}},
            self.delete_feature, ["uid"])
        add("terrain",
            "Terrain analysis on the loaded elevation model. analysis: viewshed (what a point can see), "
            "line_of_sight (A to B), mobility (GO/SLOW-GO/NO-GO), slope, landing_zones (HLZ candidates), route "
            "(least-cost cross-country route with time), range_rings, grid (GRG), cordon (inner/outer rings), "
            "elevation (height at a point). Needs get_elevation first, except range_rings, grid and cordon.",
            {"analysis": {"type": "string", "enum": ["viewshed", "line_of_sight", "mobility", "slope", "landing_zones",
                                                     "route", "range_rings", "grid", "cordon", "elevation"]},
             "location": LOC, "location2": LOC,
             "radius_m": {"type": "number"}, "observer_height": {"type": "number"},
             "mover": {"type": "string", "enum": sorted(terrain.MOBILITY)},
             "radii_m": {"type": "array", "items": {"type": "number"}},
             "cell_m": {"type": "number"}, "max_slope": {"type": "number"},
             "min_diameter_m": {"type": "number"}, "inner_m": {"type": "number"}, "outer_m": {"type": "number"},
             "name": {"type": "string"}},
            self.terrain, ["analysis"])
        add("get_elevation",
            "Download an elevation model (DEM) for an area so terrain analysis can run. Default area: current view.",
            {"area": LOC, "radius_m": {"type": "number"}}, self.get_elevation)
        add("incident_analysis",
            "Analyse recorded incidents. hotspots: spatial clusters; temporal: hour/day pattern and peak windows; "
            "route_threat: incidents within a corridor of each drawn route.",
            {"analysis": {"type": "string", "enum": ["hotspots", "temporal", "route_threat"]},
             "type_filter": {"type": "string"}, "distance_m": {"type": "number"},
             "min_incidents": {"type": "integer"}},
            self.incident_analysis, ["analysis"])
        add("imagery",
            "Google Earth Engine data. action=list shows products; add streams a product for the view (optionally a "
            "date range); change builds a before/after change-detection layer (method optical or sar); download "
            "fetches pixels for local analysis.",
            {"action": {"type": "string", "enum": ["list", "add", "change", "download"]},
             "product": {"type": "string"}, "start": {"type": "string", "description": "YYYY-MM-DD"},
             "end": {"type": "string"}, "before_start": {"type": "string"}, "before_end": {"type": "string"},
             "after_start": {"type": "string"}, "after_end": {"type": "string"},
             "method": {"type": "string", "enum": ["optical", "sar"]}, "area": LOC, "radius_m": {"type": "number"}},
            self.imagery, ["action"])
        add("reference_data",
            "Load reference layers. action=basemap sets the background map; action=osm imports OpenStreetMap "
            "features (roads, waterways, bridges, buildings, places, health, schools, worship, government, police, "
            "fuel, towers) for the view.",
            {"action": {"type": "string", "enum": ["basemap", "osm"]}, "name": {"type": "string"},
             "area": LOC, "radius_m": {"type": "number"}},
            self.reference_data, ["action", "name"])
        add("search_gis_tools",
            "Search every geoprocessing algorithm in the QGIS engine (about a thousand: buffers, overlays, raster "
            "maths, networks, statistics ...) when no dedicated tool fits.",
            {"query": {"type": "string"}}, self.search_gis_tools, ["query"])
        add("describe_gis_tool", "Get the parameters of a QGIS algorithm before running it.",
            {"id": {"type": "string"}}, self.describe_gis_tool, ["id"])
        add("run_gis_tool",
            "Run any QGIS algorithm by id. Layer parameters take layer names; output parameters may be omitted "
            "(results are saved to the operation folder and added to the map).",
            {"id": {"type": "string"}, "parameters": {"type": "object"}}, self.run_gis_tool, ["id", "parameters"])
        add("sensors",
            "Sensor and device links (receive-only). status: connections and live devices; options: available "
            "transports, protocols and presets; connect: open a link (transport + protocol, or a preset); "
            "disconnect: close one by id.",
            {"action": {"type": "string", "enum": ["status", "options", "connect", "disconnect"]},
             "preset": {"type": "string"}, "name": {"type": "string"},
             "transport": {"type": "string"}, "transport_options": {"type": "object"},
             "protocol": {"type": "string"}, "protocol_options": {"type": "object"},
             "id": {"type": "string"}},
            self.sensors, ["action"])
        add("run_pyqgis",
            "Last resort: run PyQGIS code when no other tool can do the job. The operator is asked to approve the "
            "code first. Available: wb (workbench), operation, iface, processing, BADR_WORKSPACE.",
            {"code": {"type": "string"}, "explanation": {"type": "string"}}, self.run_pyqgis, ["code", "explanation"])

    # ---- implementations -------------------------------------------------
    def get_picture(self):
        wb = self.wb
        ext = wb.extent_wgs84()
        parts = [f"Operation: {wb.operation.name}"]
        if ext:
            c = wb.center_wgs84()
            parts.append(f"Map view: W{ext[0]:.4f} S{ext[1]:.4f} E{ext[2]:.4f} N{ext[3]:.4f}; centre "
                         f"{to_mgrs(c[0], c[1], 4)}; about {geodesy.distance(c[0], ext[0], c[0], ext[2]) / 1000:.1f} km wide")
        parts.append(wb.operation.summary())
        parts.append("Other layers:\n" + wb.describe_layers())
        parts.append("Elevation model: " + ("loaded" if wb.dem() is not None else "NOT loaded"))
        if self.devices is not None:
            d = self.devices.describe()
            parts.append(f"Sensor links: {len(d['connections'])}; live devices: {len(d['devices'])}")
        return "\n".join(parts)

    def go_to(self, location, radius_m=3000):
        lat, lon = self.locate(location)
        self.wb.zoom_to(lat, lon, float(radius_m))
        return f"Map centred on {to_mgrs(lat, lon, 4)} ({lat:.5f}, {lon:.5f})."

    def find_symbol(self, query, symbol_sets=None):
        hits = S.search(query, symbol_sets, limit=10)
        if not hits:
            return "No matching symbols."
        return {"matches": [{"symbol_set": h["symbol_set"], "set": h["set_name"], "entity": h["entity"],
                             "name": h["name"], **({"geometry": h["geom"]} if "geom" in h else {})} for h in hits],
                "hint": "pass the name, or a SIDC built from these codes, to place_symbol"}

    def place_symbol(self, location, symbol, affiliation=None, name="", echelon=None, higher="", status=None,
                     remarks=""):
        lat, lon = self.locate(location)
        code = self._resolve_symbol(symbol, affiliation, echelon, status)
        uid = self.wb.operation.add_point("units", lat, lon, {
            "sidc": code, "name": name, "higher": higher, "remarks": remarks}, source=self.source)
        return {"uid": uid, "sidc": code, "symbol": S.describe(code), "mgrs": to_mgrs(lat, lon, 4),
                "review": "pending operator confirmation" if self.source == "ai" else "confirmed"}

    def log_incident(self, location, type, dtg=None, description="", casualties=0, affiliation="hostile",
                     reliability="", credibility=""):
        lat, lon = self.locate(location)
        hits = S.search(type, ["activity"], limit=1)
        entity = hits[0]["entity"] if hits else "110000"
        code = S.build("activity", entity, affiliation or "hostile")
        attrs = {"sidc": code, "kind": type, "name": type, "description": description,
                 "casualties": int(casualties or 0), "reliability": reliability, "credibility": credibility}
        if dtg:
            if pattern.parse_dtg(dtg) is None:
                raise ToolError(f"could not read dtg {dtg!r}; use DDHHMMZMONYY or ISO 8601")
            attrs["dtg"] = dtg
        uid = self.wb.operation.add_point("incidents", lat, lon, attrs, source=self.source)
        return {"uid": uid, "symbol": S.describe(code), "mgrs": to_mgrs(lat, lon, 4)}

    def draw_graphic(self, kind, points, radius_m=None, name="", affiliation=None, status="present", remarks=""):
        if kind not in GRAPHIC_KINDS:
            raise ToolError(f"unknown kind {kind!r}")
        pts = [self.locate(p) for p in (points or [])]
        area_kinds = {i["kind"] for i in PALETTE["Control areas"]} | {"hotspot", "hlz_candidate", "range_ring"}
        is_area = kind in area_kinds
        if is_area:
            if len(pts) == 1:
                ring = [QgsPointXY(x, y) for x, y in geodesy.circle(pts[0][0], pts[0][1], float(radius_m or 500))]
            elif len(pts) >= 3:
                ring = [QgsPointXY(lon, lat) for lat, lon in pts]
                ring.append(ring[0])
            else:
                raise ToolError("an area needs 3+ points, or 1 point with radius_m")
            geom, layer = QgsGeometry.fromPolygonXY([ring]), "areas"
        else:
            if len(pts) < 2:
                raise ToolError("a line needs at least 2 points")
            geom, layer = QgsGeometry.fromPolylineXY([QgsPointXY(lon, lat) for lat, lon in pts]), "lines"
        uid = self.wb.operation.add(layer, geom, {
            "kind": kind, "name": name, "affiliation": affiliation or "", "status": status, "remarks": remarks},
            source=self.source)
        return {"uid": uid, "layer": layer, "kind": kind}

    def update_feature(self, uid, location=None, name=None, affiliation=None, status=None, remarks=None):
        op = self.wb.operation
        key, feat = op.find_any(uid)
        if feat is None:
            raise ToolError(f"no feature with uid {uid!r}")
        attrs, geom = {}, None
        names = op.layer(key).fields().names()
        if name is not None:
            attrs["name"] = name
        if remarks is not None:
            attrs["remarks"] = remarks
        if "sidc" in names and feat["sidc"]:
            code = feat["sidc"]
            if affiliation:
                code = S.with_affiliation(code, affiliation)
            if status:
                code = S.with_status(code, status)
            if code != feat["sidc"]:
                attrs["sidc"] = code
        else:
            if affiliation:
                attrs["affiliation"] = affiliation
            if status:
                attrs["status"] = status
        if location:
            if feat.geometry().type() != 0:
                raise ToolError("only point features can be moved with update_feature")
            lat, lon = self.locate(location)
            geom = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
        op.update(key, uid, attrs or None, geom)
        return f"Updated {uid} in {key}."

    def delete_feature(self, uid):
        key, feat = self.wb.operation.find_any(uid)
        if feat is None:
            raise ToolError(f"no feature with uid {uid!r}")
        if key in ("tracks", "trails", "footprints"):
            raise ToolError("live sensor tracks are managed in the Sensors panel")
        self.wb.operation.remove(key, uid)
        return f"Deleted {uid} from {key}."

    def terrain(self, analysis, location=None, location2=None, radius_m=None, observer_height=2.0, mover=None,
                radii_m=None, cell_m=None, max_slope=None, min_diameter_m=None, inner_m=None, outer_m=None,
                name=None):
        wb = self.wb

        def need(loc, label="location"):
            if not loc:
                raise ToolError(f"{analysis} needs '{label}'")
            return self.locate(loc)
        if analysis == "viewshed":
            lat, lon = need(location)
            return terrain.viewshed(wb, lat, lon, observer_height, radius_m=float(radius_m or 5000), name=name)
        if analysis == "line_of_sight":
            a, b = need(location), need(location2, "location2")
            return terrain.line_of_sight(wb, a[0], a[1], b[0], b[1], observer_height)
        if analysis == "mobility":
            return terrain.mobility(wb, self._bbox(location, radius_m), mover or "wheeled", name)
        if analysis == "slope":
            return terrain.slope_map(wb, self._bbox(location, radius_m))
        if analysis == "landing_zones":
            return terrain.landing_zones(wb, self._bbox(location, radius_m), float(max_slope or 7),
                                         float(min_diameter_m or 50))
        if analysis == "route":
            a, b = need(location), need(location2, "location2")
            return terrain.route(wb, a[0], a[1], b[0], b[1], mover or "dismounted", name)
        if analysis == "range_rings":
            lat, lon = need(location)
            return terrain.range_rings(wb, lat, lon, radii_m or [float(radius_m or 1000)], name)
        if analysis == "grid":
            return terrain.reference_grid(wb, self._bbox(location, radius_m), float(cell_m or 500), name or "GRG")
        if analysis == "cordon":
            lat, lon = need(location)
            return terrain.cordon(wb, lat, lon, float(inner_m or 150), float(outer_m or 500), name or "",
                                  source=self.source)
        if analysis == "elevation":
            lat, lon = need(location)
            z = terrain.elevation_at(wb, lat, lon)
            return f"Elevation at {to_mgrs(lat, lon, 4)}: {'unknown' if z is None else f'{z:.0f} m'}"
        raise ToolError(f"unknown analysis {analysis!r}")

    def get_elevation(self, area=None, radius_m=None):
        from ..analysis import dem
        return dem.get_elevation(self.wb, self._bbox(area, radius_m) if area else None)

    def incident_analysis(self, analysis, type_filter=None, distance_m=None, min_incidents=None):
        if analysis == "hotspots":
            return pattern.hotspots(self.wb, float(distance_m or 500), int(min_incidents or 3), type_filter)
        if analysis == "temporal":
            out = pattern.temporal(self.wb, type_filter)
            out.pop("matrix", None)
            return out
        if analysis == "route_threat":
            return pattern.route_threat(self.wb, float(distance_m or 300))
        raise ToolError(f"unknown analysis {analysis!r}")

    def imagery(self, action, product=None, start=None, end=None, before_start=None, before_end=None,
                after_start=None, after_end=None, method="optical", area=None, radius_m=None):
        from ..gee import client as gee
        if action == "list":
            ready, msg = gee.status()
            return {"earth_engine": msg, "ready": ready,
                    "products": {k: f"{v[0]} - {v[1]}" for k, v in gee.PRODUCTS.items()}}
        if not gee.available():
            raise ToolError(gee.status()[1] + ". Imagery from Earth Engine is unavailable until this is set up; "
                            "basemaps and open elevation still work.")
        bbox = self._bbox(area, radius_m)
        if action == "add":
            if product not in gee.PRODUCTS:
                raise ToolError(f"product must be one of {sorted(gee.PRODUCTS)}")
            return gee.add_layer(self.wb, product, bbox, start, end)
        if action == "change":
            if not all((before_start, before_end, after_start, after_end)):
                raise ToolError("change needs before_start, before_end, after_start, after_end")
            return gee.change_detection(self.wb, (before_start, before_end), (after_start, after_end), method, bbox)
        if action == "download":
            if product not in gee.PRODUCTS:
                raise ToolError(f"product must be one of {sorted(gee.PRODUCTS)}")
            return gee.download_product(self.wb, product, bbox, start, end)
        raise ToolError(f"unknown action {action!r}")

    def reference_data(self, action, name, area=None, radius_m=None):
        from ..core.workbench import BASEMAPS
        if action == "basemap":
            match = next((b for b in BASEMAPS if name.lower() in b.lower()), None)
            if not match:
                raise ToolError(f"basemap must be one of {list(BASEMAPS)}")
            self.wb.set_basemap(match)
            return f"Basemap set to {match}."
        if action == "osm":
            from ..analysis import osm
            return osm.import_theme(self.wb, name.lower(), self._bbox(area, radius_m) if area else None)
        raise ToolError(f"unknown action {action!r}")

    def search_gis_tools(self, query):
        hits = CapabilityIndex.instance().search(query, 10)
        return {"matches": hits} if hits else "No algorithm matches; try different words."

    def describe_gis_tool(self, id):
        return CapabilityIndex.describe(id)

    def run_gis_tool(self, id, parameters):
        return CapabilityIndex.run(id, parameters, self.wb)

    def sensors(self, action, preset=None, name=None, transport=None, transport_options=None, protocol=None,
                protocol_options=None, id=None):
        if self.devices is None:
            raise ToolError("the sensor manager is not running")
        from ..sensors.decoders import DECODERS
        from ..sensors.manager import PRESETS
        from ..sensors.transports import TRANSPORTS
        if action == "status":
            return self.devices.describe()
        if action == "options":
            return {"transports": {k: {"label": t.label, "options": {o: h for o, (_d, h) in t.options.items()}}
                                   for k, t in TRANSPORTS.items()},
                    "protocols": {k: {"label": d.label, "options": {o: h for o, (_d, h) in d.options.items()}}
                                  for k, d in DECODERS.items()},
                    "presets": list(PRESETS)}
        if action == "connect":
            prof = {}
            if preset:
                match = next((p for p in PRESETS if preset.lower() in p.lower()), None)
                if not match:
                    raise ToolError(f"unknown preset; choose from {list(PRESETS)}")
                prof = json.loads(json.dumps(PRESETS[match]))
                prof["name"] = name or match
            if transport:
                prof["transport"] = transport
            if protocol:
                prof["decoder"] = protocol
            prof.setdefault("transport_opts", {}).update(transport_options or {})
            prof.setdefault("decoder_opts", {}).update(protocol_options or {})
            if name:
                prof["name"] = name
            if "transport" not in prof or "decoder" not in prof:
                raise ToolError("connect needs a preset, or both transport and protocol")
            cid = self.devices.add_connection(prof)
            return {"id": cid, "profile": self.devices.connections[cid].profile,
                    "note": "link opened; devices appear on the map when data arrives"}
        if action == "disconnect":
            if not self.devices.remove_connection(id or ""):
                raise ToolError(f"no connection with id {id!r}")
            return f"Connection {id} closed."
        raise ToolError(f"unknown action {action!r}")

    def run_pyqgis(self, code, explanation):
        from .executor import run_pyqgis
        if re.search(r"\b(subprocess|os\.system|shutil\.rmtree|eval\(|__import__)\b", code):
            raise ToolError("code uses a blocked call (subprocess / os.system / rmtree / eval / __import__)")
        if Settings.get("ai/confirm_code"):
            if self.confirm_code is None or not self.confirm_code(code, explanation):
                raise ToolError("the operator did not approve running this code")
        ok, out = run_pyqgis(code, self.wb)
        if not ok:
            raise ToolError(out)
        return out
