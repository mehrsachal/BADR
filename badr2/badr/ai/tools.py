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


RESULT_CAP = 2200


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
    def _add(self, name, description, params, fn, required=(), hidden=False):
        self._tools[name] = {"name": name, "description": description,
                             "parameters": _schema(params, required), "fn": fn, "hidden": hidden}

    def specs(self):
        """Definitions sent to the model (UI-only tools excluded)."""
        return [{k: v for k, v in t.items() if k not in ("fn", "hidden")}
                for t in self._tools.values() if not t["hidden"]]

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
        if len(text) > RESULT_CAP:          # results are re-read on every later turn: keep them small
            text = text[:RESULT_CAP] + " ...[truncated]"
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
        """Tool definitions are sent with every model request, so they are kept
        deliberately terse: shared conventions (location formats, affiliations)
        live once in the system prompt instead of being repeated per parameter.
        Tools flagged ``hidden`` serve the UI only and are never sent."""
        add = self._add
        S_, N_, I_ = {"type": "string"}, {"type": "number"}, {"type": "integer"}
        O_ = {"type": "object"}
        LIST = {"type": "array", "items": S_}

        add("get_picture", "Read what is on the map: units, incidents, measures, tracks, layers, view.",
            {}, self.get_picture)
        add("go_to", "Move the map.", {"location": S_, "radius_m": N_}, self.go_to, ["location"])
        add("place_symbol", "Place a NATO APP-6D symbol. symbol: plain words ('mechanized infantry', 'safe "
            "house', 'checkpoint') or a 20-digit SIDC.",
            {"location": S_, "symbol": S_, "affiliation": S_, "name": S_, "echelon": S_, "higher": S_,
             "status": {"type": "string", "enum": ["present", "planned"]}, "remarks": S_},
            self.place_symbol, ["location", "symbol"])
        add("log_incident", "Record an incident (IED, shooting, kidnapping ...). dtg: DDHHMMZMONYY or ISO.",
            {"location": S_, "type": S_, "dtg": S_, "description": S_, "casualties": I_, "affiliation": S_},
            self.log_incident, ["location", "type"])
        kinds = sorted(k for k in GRAPHIC_KINDS if k not in ("los_visible", "los_hidden", "grg", "analysis",
                                                             "hotspot", "hlz_candidate", "range_ring"))
        add("draw_graphic", "Draw a control measure. Lines: 2+ points. Areas: 3+ points, or 1 point + radius_m.",
            {"kind": {"type": "string", "enum": kinds}, "points": LIST, "radius_m": N_, "name": S_,
             "affiliation": S_, "status": {"type": "string", "enum": ["present", "planned"]}},
            self.draw_graphic, ["kind", "points"])
        add("sketch", "Free drawing that is not a NATO symbol (note, marker, line, area). Creates the layer "
            "if it does not exist.",
            {"layer": S_, "geometry": {"type": "string", "enum": ["point", "line", "polygon"]},
             "points": LIST, "name": S_, "notes": S_, "color": S_}, self.sketch, ["layer", "points"])
        add("edit_feature", "Change, move or delete a feature by uid.",
            {"uid": S_, "location": S_, "name": S_, "affiliation": S_, "status": S_, "remarks": S_,
             "delete": {"type": "boolean"}}, self.edit_feature, ["uid"])
        add("terrain", "Terrain analysis. load_elevation first (not needed for range_rings, grid, cordon). "
            "line_of_sight and route need location and location2.",
            {"analysis": {"type": "string", "enum": ["load_elevation", "viewshed", "line_of_sight", "mobility",
                                                     "slope", "landing_zones", "route", "range_rings", "grid",
                                                     "cordon", "elevation"]},
             "location": S_, "location2": S_, "radius_m": N_, "observer_height": N_,
             "mover": {"type": "string", "enum": sorted(terrain.MOBILITY)},
             "radii_m": {"type": "array", "items": N_}, "cell_m": N_, "name": S_},
            self.terrain, ["analysis"])
        add("incidents", "Incident analysis: hotspots (clusters), temporal (when), route_threat (per route).",
            {"analysis": {"type": "string", "enum": ["hotspots", "temporal", "route_threat"]},
             "type_filter": S_, "distance_m": N_}, self.incident_analysis, ["analysis"])
        add("imagery", "Earth Engine: list products, add one to the map, change detection (optical|sar), "
            "or download pixels. Dates YYYY-MM-DD.",
            {"action": {"type": "string", "enum": ["list", "add", "change", "download"]}, "product": S_,
             "start": S_, "end": S_, "before_start": S_, "before_end": S_, "after_start": S_,
             "after_end": S_, "method": S_}, self.imagery, ["action"])
        add("reference", "Background data. basemap: name. osm: roads, waterways, bridges, buildings, places, "
            "health, schools, worship, government, police, fuel, towers.",
            {"action": {"type": "string", "enum": ["basemap", "osm"]}, "name": S_},
            self.reference_data, ["action", "name"])
        add("qgis", "Any other GIS operation through the QGIS engine: search by words, describe by id, "
            "run by id with parameters (layers by name; outputs optional).",
            {"action": {"type": "string", "enum": ["search", "describe", "run"]}, "query": S_, "id": S_,
             "parameters": O_}, self.qgis, ["action"])
        add("sensors", "Receive-only sensor links: status, options, connect (preset, or transport + "
            "protocol), disconnect (id).",
            {"action": {"type": "string", "enum": ["status", "options", "connect", "disconnect"]},
             "preset": S_, "name": S_, "transport": S_, "transport_options": O_, "protocol": S_,
             "protocol_options": O_, "id": S_}, self.sensors, ["action"])
        add("export", "Export layers to a GIS format: gpkg, shp, geojson, kml, csv, gdb. layers: names, "
            "default the whole picture.",
            {"format": S_, "layers": LIST}, self.export, ["format"])
        add("run_pyqgis", "Last resort: PyQGIS code (operator approves). Has wb, operation, iface, processing.",
            {"code": S_, "explanation": S_}, self.run_pyqgis, ["code", "explanation"])
        add("brief", "Give your answer. 1-5 beats, each one short sentence (max 16 words) the operator can "
            "play back; 'at' is the place or uid the map should fly to for that sentence.",
            {"beats": {"type": "array", "items": {"type": "object", "properties": {
                "say": S_, "at": S_, "zoom_m": N_}, "required": ["say"]}}},
            self.brief, ["beats"])

        # ---- UI-only entry points (not sent to the model) -------------------
        hid = lambda *a, **k: self._add(*a, hidden=True, **k)       # noqa: E731
        hid("find_symbol", "", {}, self.find_symbol)
        hid("update_feature", "", {}, self.update_feature)
        hid("delete_feature", "", {}, self.delete_feature)
        hid("get_elevation", "", {}, self.get_elevation)
        hid("incident_analysis", "", {}, self.incident_analysis)
        hid("reference_data", "", {}, self.reference_data)
        hid("search_gis_tools", "", {}, self.search_gis_tools)
        hid("describe_gis_tool", "", {}, self.describe_gis_tool)
        hid("run_gis_tool", "", {}, self.run_gis_tool)

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
        if analysis == "load_elevation":
            return self.get_elevation(location, radius_m)
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
            return {"transports": {k: list(t.options) for k, t in TRANSPORTS.items()},
                    "protocols": {k: list(d.options) for k, d in DECODERS.items()},
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

    def edit_feature(self, uid, location=None, name=None, affiliation=None, status=None, remarks=None,
                     delete=False):
        if delete:
            return self.delete_feature(uid)
        return self.update_feature(uid, location, name, affiliation, status, remarks)

    def qgis(self, action, query=None, id=None, parameters=None):
        if action == "search":
            hits = CapabilityIndex.instance().search(query or "", 8)
            return {"matches": [{"id": h["id"], "name": h["name"]} for h in hits]} if hits else "No match."
        if not id:
            raise ToolError(f"{action} needs 'id'")
        if action == "describe":
            d = CapabilityIndex.describe(id)
            d["help"] = d["help"][:300]
            d.pop("outputs", None)
            return d
        if action == "run":
            return CapabilityIndex.run(id, parameters or {}, self.wb)
        raise ToolError(f"unknown action {action!r}")

    def sketch(self, layer, points, geometry=None, name="", notes="", color=None):
        from ..core import exchange
        pts = [self.locate(p) for p in (points or [])]
        if not pts:
            raise ToolError("sketch needs at least one point")
        target = next((l for l in exchange.user_layers(self.wb) if l.name().lower() == str(layer).lower()), None)
        if target is None:
            geometry = geometry or ("point" if len(pts) == 1 else "line")
            target = exchange.create_user_layer(self.wb, layer, geometry, color or "#ffd24a")
        exchange.add_user_feature(self.wb, target, pts, name, notes)
        return f"Added to sketch layer '{target.name()}' ({target.featureCount()} features)."

    def export(self, format, layers=None, folder=None, selected_only=False):
        import os
        from ..core import exchange
        if layers:
            chosen = []
            for n in layers:
                lyr = self.wb.find_layer(n)
                if lyr is None:
                    raise ToolError(f"no layer named {n!r}")
                chosen.append(lyr)
        else:
            cop = [self.wb.operation.layer(k) for k in ("units", "incidents", "lines", "areas", "detections")]
            chosen = [l for l in cop + exchange.user_layers(self.wb) if l.featureCount() > 0]
        if not folder:
            base = self.wb.operation.folder or os.path.join(os.path.expanduser("~"), "BADR")
            folder = os.path.join(base, "export")
        folder = os.path.normpath(folder)
        files = exchange.export_layers(chosen, folder, format, self.wb.operation.name, selected_only)
        return {"files": files, "summary": f"Exported {len(chosen)} layer(s) to {folder}."}

    def brief(self, beats):
        """Validated here; the UI turns the beats into a playable story."""
        out = []
        for b in (beats or [])[:6]:
            say = str((b or {}).get("say", "")).strip()
            if not say:
                continue
            beat = {"say": say}
            at = (b or {}).get("at")
            if at:
                try:
                    lat, lon = self.locate(at)
                    beat["lat"], beat["lon"] = lat, lon
                    beat["zoom_m"] = float(b.get("zoom_m") or 1500)
                    key, feat = self.wb.operation.find_any(str(at))
                    if feat is not None:
                        beat["uid"] = str(at)
                except (ToolError, ValueError):
                    pass                      # a sentence without a resolvable place still gets shown
            out.append(beat)
        if not out:
            raise ToolError("brief needs at least one beat with 'say'")
        self.last_brief = out
        return "shown"
