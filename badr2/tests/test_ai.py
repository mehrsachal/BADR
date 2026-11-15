import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from badr.ai.llm import AnthropicClient, OpenAICompatClient, Reply, ToolCall

LAT, LON = 34.00, 71.50


@pytest.fixture()
def wb(qgis_app, tmp_path):
    from badr.core.workbench import Workbench
    w = Workbench()
    w.open_operation(str(tmp_path / "op"), "Op TEST")
    w.zoom_to(LAT, LON, 5000)
    return w


@pytest.fixture()
def box(wb):
    from badr.ai.tools import Toolbox
    from badr.sensors.manager import DeviceManager
    mgr = DeviceManager(wb)
    yield Toolbox(wb, mgr)
    mgr.shutdown()


def ok(box, tool, /, **args):
    text, err = box.call(tool, args)
    assert not err, text
    try:
        return json.loads(text)
    except ValueError:
        return text


# ---- tool layer -----------------------------------------------------------
def test_tool_specs_are_valid_json_schema(box):
    specs = box.specs()
    assert 12 <= len(specs) <= 17
    # every definition is re-sent on each model call: keep the whole set small
    assert len(json.dumps(specs)) / 4 < 2000, "tool definitions grew past the token budget"
    assert not any(s["name"] in ("find_symbol", "update_feature") for s in specs)   # UI-only tools stay local
    for s in specs:
        assert s["parameters"]["type"] == "object" and s["description"]
        assert set(s["parameters"]["required"]) <= set(s["parameters"]["properties"])
    json.dumps(specs)


def test_place_symbol_by_description_is_pending_review(box, wb):
    r = ok(box, "place_symbol", location="34.01, 71.52", symbol="mechanized infantry", affiliation="hostile",
           name="EN 1", echelon="company")
    assert r["sidc"][3] == "6" and r["sidc"][8:10] == "15" and "review" in r
    f = wb.operation.find("units", r["uid"])
    assert f["source"] == "ai" and f["review"] == "pending"
    assert len(wb.operation.pending()) == 1
    wb.operation.resolve("units", r["uid"], accept=True)
    assert wb.operation.pending() == []


def test_location_forms_mgrs_name_uid_view(box, wb):
    from badr.core.mgrs import to_mgrs
    r = ok(box, "place_symbol", location=to_mgrs(LAT, LON, 5), symbol="Safe house", name="OBJ FOX")
    lat, lon = box.locate("OBJ FOX")
    assert abs(lat - LAT) < 1e-4 and abs(lon - LON) < 1e-4
    assert box.locate(r["uid"])[0] == pytest.approx(lat)
    assert box.locate("view")[0] == pytest.approx(LAT, abs=1e-3)
    text, err = box.call("go_to", {"location": "nowhere-land"})
    assert err and "could not resolve" in text


def test_reject_and_delete(box, wb):
    r = ok(box, "place_symbol", location="view", symbol="checkpoint", affiliation="friend")
    wb.operation.resolve("units", r["uid"], accept=False)
    assert wb.operation.layer("units").featureCount() == 0
    text, err = box.call("delete_feature", {"uid": "missing"})
    assert err


def test_incidents_graphics_and_analysis_tools(box, wb):
    for i in range(4):
        ok(box, "log_incident", location=f"{LAT + i * 0.0004}, {LON}", type="IED explosion",
           dtg=f"{10 + i:02d}1900ZNOV26", casualties=2)
    assert wb.operation.layer("incidents").featureCount() == 4
    f = next(wb.operation.layer("incidents").getFeatures())
    assert f["sidc"][4:6] == "40" and f["sidc"][10:16] == "110301"
    ok(box, "draw_graphic", kind="route", name="RTE IRON",
       points=[f"{LAT - 0.01}, {LON}", f"{LAT + 0.01}, {LON}"])
    ok(box, "draw_graphic", kind="nai", name="1", points=["view"], radius_m=400)
    assert wb.operation.layer("lines").featureCount() == 1 and wb.operation.layer("areas").featureCount() == 1
    hs = ok(box, "incident_analysis", analysis="hotspots", distance_m=300)
    assert hs["hotspots"][0]["incidents"] == 4
    tm = ok(box, "incident_analysis", analysis="temporal")
    assert tm["total"] == 4 and "matrix" not in tm
    rt = ok(box, "incident_analysis", analysis="route_threat", distance_m=200)
    assert rt["routes"][0]["route"] == "RTE IRON" and rt["routes"][0]["incidents"] == 4
    text, err = box.call("log_incident", {"location": "view", "type": "arson", "dtg": "yesterday-ish"})
    assert err and "dtg" in text
    text, err = box.call("draw_graphic", {"kind": "route", "points": ["view"]})
    assert err and "2 points" in text


def test_terrain_tool_reports_missing_dem_and_runs_dem_free_analyses(box, wb):
    text, err = box.call("terrain", {"analysis": "viewshed", "location": "view"})
    assert err and "elevation" in text.lower()
    r = ok(box, "terrain", analysis="range_rings", location="view", radii_m=[500, 1500])
    assert wb.find_layer(r["layer"]).featureCount() == 2
    ok(box, "terrain", analysis="cordon", location="view", inner_m=100, outer_m=300, name="OBJ")
    assert len(wb.operation.pending()) == 2


def test_gis_tool_search_describe_run(box, wb):
    ok(box, "place_symbol", location="view", symbol="infantry", affiliation="friend")
    hits = ok(box, "search_gis_tools", query="buffer")
    assert hits["matches"][0]["id"] == "native:buffer"
    d = ok(box, "describe_gis_tool", id="native:buffer")
    assert any(p["name"] == "DISTANCE" for p in d["parameters"])
    r = ok(box, "run_gis_tool", id="native:buffer", parameters={"INPUT": "Units & Equipment", "DISTANCE": 0.01})
    assert r["loaded_layers"]
    text, err = box.call("run_gis_tool", {"id": "native:nope", "parameters": {}})
    assert err


def test_sensor_tool_connect_status_disconnect(box):
    opts = ok(box, "sensors", action="options")
    assert {"udp", "tcp", "serial", "http", "mqtt", "file"} <= set(opts["transports"])
    assert {"cot", "mavlink", "nmea", "sbs", "json"} <= set(opts["protocols"])
    r = ok(box, "sensors", action="connect", transport="udp", protocol="cot", name="t",
           transport_options={"host": "127.0.0.1", "port": 0})
    st = ok(box, "sensors", action="status")
    assert st["connections"][0]["protocol"] == "cot"
    ok(box, "sensors", action="disconnect", id=r["id"])
    from badr.core.settings import Settings
    Settings.set_connections([])


def test_imagery_tool_without_earth_engine_is_explicit(box, monkeypatch):
    from badr.gee import client as gee
    monkeypatch.setattr(gee, "_ee", None)
    monkeypatch.setattr(gee, "available", lambda: False)
    listing = ok(box, "imagery", action="list")
    assert "s2_true" in listing["products"] and listing["ready"] is False
    text, err = box.call("imagery", {"action": "add", "product": "s2_true"})
    assert err and "Earth Engine" in text


def test_run_pyqgis_requires_operator_approval(box, wb):
    from badr.core.settings import Settings
    Settings.set("ai/confirm_code", True)
    text, err = box.call("run_pyqgis", {"code": "print(1+1)", "explanation": "x"})
    assert err and "approve" in text
    box.confirm_code = lambda code, why: True
    assert ok(box, "run_pyqgis", code="print(operation.name)", explanation="x") == "Op TEST"
    text, err = box.call("run_pyqgis", {"code": "import subprocess; subprocess.run(['ls'])", "explanation": "x"})
    assert err and "blocked" in text


def test_unknown_tool_and_bad_args_do_not_raise(box):
    assert box.call("launch", {})[1] is True
    assert box.call("go_to", {"wrong": 1})[1] is True
    assert box.call("go_to", {"_invalid_json": "{"})[1] is True


# ---- agent loop -----------------------------------------------------------
class ScriptedClient:
    """Plays back a fixed sequence of replies; records what the agent sends."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def reset(self):
        pass

    def send_user(self, text, image_b64=None):
        self.sent.append(("user", text))
        return self.replies.pop(0)

    def send_tool_results(self, results):
        self.sent.append(("tools", results))
        return self.replies.pop(0)


def test_agent_executes_tools_recovers_from_error_and_reports(box, wb):
    from badr.ai.agent import Agent
    client = ScriptedClient([
        Reply(text="Placing the unit.", tool_calls=[
            ToolCall("1", "place_symbol", {"location": "view", "symbol": "infantry", "affiliation": "friend",
                                           "name": "A COY"}),
            ToolCall("2", "terrain", {"analysis": "viewshed", "location": "A COY"})], stop="tool_use"),
        Reply(tool_calls=[ToolCall("3", "terrain", {"analysis": "range_rings", "location": "A COY",
                                                    "radii_m": [1000]})], stop="tool_use"),
        Reply(text="BLUF: A COY placed (pending review); 1 km ring drawn. No elevation loaded, so no viewshed."),
    ])
    events = []
    agent = Agent(box, client=client, on_event=lambda k, d: events.append((k, d)))
    out = agent.run("Put A Coy at the centre and show what it can see")
    assert out.startswith("BLUF")
    first = client.sent[1][1]
    assert [r[0] for r in first] == ["1", "2"]            # both results returned together
    assert first[0][3] is False and first[1][3] is True   # viewshed failed, error surfaced to the model
    kinds = [k for k, _ in events]
    assert kinds.count("tool") == 3 and kinds[-1] == "final" and "note" in kinds
    assert wb.operation.layer("units").featureCount() == 1


def test_agent_stops_runaway_loops_and_reports_llm_errors(box):
    from badr.ai.agent import Agent, MAX_STEPS
    from badr.ai.llm import LlmError
    loop = ScriptedClient([Reply(tool_calls=[ToolCall(str(i), "get_picture", {})], stop="tool_use")
                           for i in range(MAX_STEPS + 2)])
    events = []
    Agent(box, client=loop, on_event=lambda k, d: events.append(k)).run("loop")
    assert events[-1] == "error"

    class Broken(ScriptedClient):
        def send_user(self, text, image_b64=None):
            raise LlmError("No API key set")
    events2 = []
    Agent(box, client=Broken([]), on_event=lambda k, d: events2.append((k, d))).run("hi")
    assert events2[-1] == ("error", "No API key set")


def test_system_prompt_states_scope_and_review():
    from badr.ai.agent import build_system_prompt
    p = build_system_prompt()
    assert "planning and analysis" in p and "review" in p and "receive-only" in p
    assert len(p) / 4 < 400, "system prompt grew past the token budget"


# ---- wire formats ---------------------------------------------------------
TOOLS = [{"name": "go_to", "description": "d", "parameters": {"type": "object", "properties": {}, "required": []}}]


def test_openai_compatible_wire_roundtrip():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((dict(self.headers), body))
            if len(seen) == 1:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "go_to", "arguments": "{\"location\": \"view\"}"}}]}
            else:
                msg = {"role": "assistant", "content": "done"}
            data = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = OpenAICompatClient("SYS", TOOLS, endpoint=f"http://127.0.0.1:{srv.server_port}/v1/chat/completions",
                               api_key="k", model="local", max_tokens=100, provider="custom")
        r = c.send_user("go")
        assert r.stop == "tool_use" and r.tool_calls[0].args == {"location": "view"}
        r2 = c.send_tool_results([("c1", "go_to", "ok", False)])
        assert r2.text == "done" and r2.stop == "end"
    finally:
        srv.shutdown()
    hdr, body = seen[1]
    assert hdr["Authorization"] == "Bearer k"
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["tools"][0]["function"]["name"] == "go_to"


def test_anthropic_client_uses_sdk_and_keeps_full_content(monkeypatch):
    calls = []

    def fake_create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            content = [SimpleNamespace(type="thinking", thinking="", signature="s"),
                       SimpleNamespace(type="text", text="Moving."),
                       SimpleNamespace(type="tool_use", id="tu1", name="go_to", input={"location": "view"})]
            return SimpleNamespace(content=content, stop_reason="tool_use")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done.")], stop_reason="end_turn")
    c = AnthropicClient("SYS", TOOLS, api_key="test-key", model="claude-opus-5-5")
    monkeypatch.setattr(c.client.beta.messages, "create", fake_create)
    r = c.send_user("go")
    assert r.stop == "tool_use" and r.text == "Moving." and r.tool_calls[0].id == "tu1"
    r2 = c.send_tool_results([("tu1", "go_to", "ok", False), ("tu2", "x", "bad", True)])
    assert r2.text == "Done."
    first, second = calls
    assert first["model"] == "claude-opus-5-5" and first["system"] == "SYS"
    assert first["tools"][0]["input_schema"]["type"] == "object"
    assert "thinking" not in first and "temperature" not in first and "tool_choice" not in first
    assert first["fallbacks"] == "default"
    assert first["cache_control"] == {"type": "ephemeral"} and first["output_config"]["effort"] in ("low", "medium", "high")
    msgs = second["messages"]
    assert [b.type for b in msgs[1]["content"]] == ["thinking", "text", "tool_use"]   # replayed unchanged
    results = msgs[2]["content"]
    assert [b["tool_use_id"] for b in results] == ["tu1", "tu2"] and results[1]["is_error"] is True


def test_anthropic_refusal_is_reported_not_executed(monkeypatch):
    c = AnthropicClient("SYS", TOOLS, api_key="test-key", model="claude-opus-5-5")
    monkeypatch.setattr(c.client.beta.messages, "create", lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t", name="go_to", input={})], stop_reason="refusal"))
    r = c.send_user("x")
    assert r.stop == "refusal" and r.tool_calls == [] and "declined" in r.text


# ---- Earth Engine (fake ee module) ----------------------------------------
class _Chain:
    """Stands in for any ee object: every call/attribute returns another chain."""

    def __init__(self, log, name="ee"):
        self._log, self._name = log, name

    def __getattr__(self, attr):
        return _Chain(self._log, f"{self._name}.{attr}")

    def __call__(self, *a, **k):
        self._log.append((self._name, a))
        return _Chain(self._log, self._name)

    def getMapId(self, vis):
        self._log.append(("getMapId", vis))
        return {"tile_fetcher": SimpleNamespace(url_format="https://earthengine.googleapis.com/v1/projects/p/maps/m/tiles/{z}/{x}/{y}")}


def test_gee_products_build_tile_layers(wb, monkeypatch):
    from badr.gee import client as gee
    log = []
    monkeypatch.setattr(gee, "_ee", _Chain(log))
    for product in gee.PRODUCTS:
        r = gee.add_layer(wb, product, (71.4, 33.9, 71.6, 34.1), "2026-09-01", "2026-10-01")
        layer = wb.find_layer(r["layer"])
        assert layer is not None and layer.providerType() == "wms"
        assert "earthengine.googleapis.com" in layer.source() and "{z}/{x}/{y}" in layer.source()
    assert any(name.endswith("ImageCollection") and a == ("COPERNICUS/S2_SR_HARMONIZED",) for name, a in log)
    assert any(name.endswith("ImageCollection") and a == ("COPERNICUS/S1_GRD",) for name, a in log)
    r = gee.change_detection(wb, ("2026-06-01", "2026-06-30"), ("2026-09-01", "2026-09-30"), "sar",
                             (71.4, 33.9, 71.6, 34.1))
    assert "sar" in r["summary"]
    with pytest.raises(ValueError):
        gee.build("nonsense", (0, 0, 1, 1))


def test_default_model_is_the_fast_tier_and_skips_unsupported_fallbacks(monkeypatch):
    calls = []
    c = AnthropicClient("SYS", TOOLS, api_key="test-key")
    assert c.model == "claude-haiku-5-5"
    monkeypatch.setattr(c.client.messages, "create", lambda **kw: calls.append(kw) or SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=9, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0)))
    r = c.send_user("hi")
    assert "fallbacks" not in calls[0] and "betas" not in calls[0]
    assert (r.tokens_in, r.tokens_out) == (1200, 9)


def test_deferred_tool_results_ride_with_the_next_user_message(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append([dict(m) for m in kw["messages"]])
        if len(calls) == 1:
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="b1", name="brief", input={})],
                                   stop_reason="tool_use")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")
    c = AnthropicClient("SYS", TOOLS, api_key="test-key")
    monkeypatch.setattr(c.client.messages, "create", fake)
    c.send_user("brief me")
    c.defer_tool_results([("b1", "brief", "shown", False)])
    assert len(calls) == 1                                   # no extra model call to close the turn
    c.send_user("and now?")
    last = calls[1][-1]["content"]
    assert last[0]["type"] == "tool_result" and last[0]["tool_use_id"] == "b1" and last[-1]["text"] == "and now?"


def test_brief_ends_the_request_in_one_model_call(box, wb):
    from badr.ai.agent import Agent
    wb.operation.add_point("units", LAT, LON, {"sidc": "10062000001117000000", "name": "OBJ FOX"})
    client = ScriptedClient([Reply(tool_calls=[
        ToolCall("1", "terrain", {"analysis": "range_rings", "location": "OBJ FOX", "radii_m": [500]}),
        ToolCall("2", "brief", {"beats": [{"say": "Ring drawn around OBJ FOX.", "at": "OBJ FOX", "zoom_m": 900},
                                          {"say": "No elevation loaded yet."}]})], stop="tool_use",
        tokens_in=1500, tokens_out=60)])
    client.deferred = None
    client.defer_tool_results = lambda results: setattr(client, "deferred", results)
    events = []
    out = Agent(box, client=client, on_event=lambda k, d: events.append((k, d))).run("ring OBJ FOX")
    assert out.startswith("Ring drawn")
    story = dict(events)["story"]
    assert story[0]["lat"] == pytest.approx(LAT) and story[0]["zoom_m"] == 900 and "lat" not in story[1]
    assert dict(events)["usage"]["calls"] == 1 and len(client.sent) == 1
    assert [r[1] for r in client.deferred] == ["terrain", "brief"]


def test_brief_does_not_hide_a_failed_action(box):
    from badr.ai.agent import Agent
    client = ScriptedClient([
        Reply(tool_calls=[ToolCall("1", "terrain", {"analysis": "viewshed", "location": "view"}),
                          ToolCall("2", "brief", {"beats": [{"say": "Viewshed done."}]})], stop="tool_use"),
        Reply(tool_calls=[ToolCall("3", "brief", {"beats": [{"say": "No elevation loaded; load it first."}]})],
              stop="tool_use")])
    client.defer_tool_results = lambda results: None
    events = []
    out = Agent(box, client=client, on_event=lambda k, d: events.append((k, d))).run("viewshed")
    assert "No elevation" in out and len(client.sent) == 2      # the model saw the error and corrected itself


def test_sketch_export_and_qgis_tools(box, wb, tmp_path):
    ok(box, "sketch", layer="Key terrain", points=["view"], name="Hill 402", notes="dominates RTE IRON")
    ok(box, "sketch", layer="Key terrain", points=[f"{LAT + 0.01}, {LON}"], name="Saddle")
    from badr.core import exchange
    (layer,) = exchange.user_layers(wb)
    assert layer.featureCount() == 2 and layer.geometryType() == 0
    ok(box, "place_symbol", location="view", symbol="infantry", affiliation="friend", name="A")
    r = ok(box, "export", format="geojson", folder=str(tmp_path / "out"))
    assert len(r["files"]) == 2 and all(f.endswith(".geojson") for f in r["files"])
    hits = ok(box, "qgis", action="search", query="buffer")
    assert hits["matches"][0]["id"] == "native:buffer"
    text, err = box.call("export", {"format": "dwg"})
    assert err and "unknown format" in text
