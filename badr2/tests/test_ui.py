"""Whole-application smoke tests (offscreen): the window, the fast path, the agent wiring."""
import time

import pytest


@pytest.fixture()
def win(qgis_app, tmp_path):
    from badr.core.settings import Settings
    from badr.ui.main_window import MainWindow
    Settings.set("ai/anthropic/key", "")
    Settings.set("ai/provider", "anthropic")
    w = MainWindow(str(tmp_path / "op"))
    w.show()
    w.wb.zoom_to(35.262, -116.684, 4000)
    qgis_app.processEvents()
    yield w
    w.close()


def pump(app, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_window_builds_all_seven_modes(win):
    from badr.ui.panels import MODES
    assert [m[0] for m in MODES] == ["PICTURE", "PLAN", "DRAW", "TERRAIN", "INTEL", "IMAGERY", "SENSORS", "TOOLS"]
    assert win.stack.count() == 8
    assert list(win.panels.made) == ["PICTURE"]              # the rest are built on first use
    for i in range(8):
        win.mode_group.button(i).click()
        assert win.stack.currentIndex() == i
    assert len(win.panels.made) == 8
    assert win.panels["PLAN"].list.count() > 5
    win.panels["PLAN"].search.setText("IED")
    assert win.panels["PLAN"].list.count() > 0
    assert win.panels["TOOLS"].tree.topLevelItemCount() >= 6
    assert win.grab().width() > 800


def test_fast_path_runs_without_a_model(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.agent, "run", lambda *a, **k: calls.append(a))
    win.ask("range rings 1, 3 and 5 km at 35.262, -116.684")
    rings = [l for l in win.wb.project.mapLayers().values() if l.name().startswith("Range rings")]
    assert rings and rings[0].featureCount() == 3
    win.ask("11S NU 28 03")
    win.ask("cordon at view")
    assert win.wb.operation.layer("areas").featureCount() == 2
    assert all(f["review"] == "confirmed" for f in win.wb.operation.layer("areas").getFeatures())
    assert calls == []                                    # nothing reached the LLM


def test_quick_router_only_claims_exact_orders():
    from badr.ai.quick import route
    assert route("go to OBJ FOX") == ("go_to", {"location": "OBJ FOX"})
    assert route("rings 500 m, 2 km around CP 1")[1]["radii_m"] == [500.0, 2000.0]
    assert route("viewshed from OP 1, 4 km") == ("terrain", {"analysis": "viewshed", "location": "OP 1", "radius_m": 4000.0})
    assert route("tracked route from A to B")[1]["mover"] == "tracked"
    assert route("mobility wheeled")[1] == {"analysis": "mobility", "mover": "wheeled"}
    assert route("hotspots") == ("incident_analysis", {"analysis": "hotspots"})
    assert route("34.5, 71.2")[0] == "go_to"
    for free_text in ("where is the IED threat highest and which route is most exposed?",
                      "put a hostile mortar team north of OBJ FOX", "brief me", "what can OP 1 see and why?"):
        assert route(free_text) is None


def test_unconfigured_ai_gives_guidance_not_a_crash(win):
    win.ask("what is the threat picture?")
    assert "Settings" in win.feed.toPlainText() and win._busy is False


def test_agent_runs_off_thread_and_tools_run_on_gui_thread(win, qgis_app, monkeypatch):
    import threading
    from badr.ai.llm import Reply, ToolCall
    from badr.core.settings import Settings
    monkeypatch.setattr(Settings, "ai_ready", classmethod(lambda cls: True))
    gui_thread = threading.current_thread()
    seen = {}
    real_call = win.ai_tools.call

    def spy(name, args):
        seen["tool_thread"] = threading.current_thread()
        return real_call(name, args)
    monkeypatch.setattr(win.ai_tools, "call", spy)

    class Client:
        def reset(self):
            pass

        def send_user(self, text, image_b64=None):
            seen["llm_thread"] = threading.current_thread()
            return Reply(tool_calls=[ToolCall("1", "place_symbol", {
                "location": "view", "symbol": "infantry", "affiliation": "hostile", "name": "EN 1"})],
                stop="tool_use", tokens_in=900, tokens_out=40)

        def send_tool_results(self, results):
            return Reply(text="**BLUF:** hostile infantry placed, pending your review.", tokens_in=60,
                         tokens_cached=900, tokens_out=20)
    win.agent._client = Client()
    win.ask("mark enemy infantry at the centre")
    assert pump(qgis_app, lambda: not win._busy)
    assert seen["llm_thread"] is not gui_thread and seen["tool_thread"] is gui_thread
    assert win.wb.operation.layer("units").featureCount() == 1
    assert win.review_list.count() == 1 and "1 pending" in win.review_head.text()
    text = win.feed.toPlainText()
    assert "900 cached" in text and "2 call(s)" in text
    assert "BLUF" in win.story.beats[0]["say"]               # the answer is shown as playable beats
    win._resolve(True)
    assert win.review_list.count() == 0


def test_simulator_feeds_live_tracks_through_real_sockets(win, qgis_app):
    win.toggle_simulator()
    assert pump(qgis_app, lambda: len(win.devices.devices) >= 3, timeout=12)
    names = {d.name for d in win.devices.devices.values()}
    assert {"RAVEN-7", "PATROL 1", "TOWER CAM"} <= names
    assert win.wb.operation.layer("footprints").featureCount() == 1
    assert win.panels["SENSORS"].devs.rowCount() >= 3
    win.toggle_simulator()
    assert win.wb.operation.layer("tracks").featureCount() == 0
