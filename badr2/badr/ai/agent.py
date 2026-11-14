"""The planning assistant: an LLM in a tool loop over the Toolbox.

Threading contract: ``run`` may be called from a worker thread.  LLM requests
happen on the calling thread; every tool call is passed to ``invoke``, which
the UI implements as "run on the GUI thread and wait" (QGIS objects are not
thread-safe).  Headless callers leave ``invoke`` as a direct call.
"""
from datetime import datetime, timezone

from .llm import LlmError, make_client

MAX_STEPS = 24

SYSTEM_PROMPT = """\
You are BADR, the planning and analysis assistant inside a military planning GIS. You work for a staff \
officer or analyst and act through tools on a shared map - the common operational picture (COP).

Scope: planning and analysis. You help with intelligence preparation of the operational environment, \
terrain and mobility analysis, incident pattern analysis, course-of-action sketching, control measures, \
collection planning and briefing products. You do not control platforms or sensors (links are \
receive-only) and you do not direct engagements; a human decides and acts.

How to work:
- Act, don't describe. If the request can be carried out with tools, carry it out, then report.
- Read before you reason: call get_picture when the request depends on what is on the map.
- Use NATO APP-6D symbology and doctrine terms. Locations are MGRS by default; accept and give MGRS.
- Sub-conventional operations (COIN, CT, stability) are the main use: treat incidents, human terrain, \
routes and patterns of life as first-class, not just units and phase lines.
- Everything you add is held as "pending review" until the operator accepts it. Say what you added and \
that it awaits review; never present your own additions as confirmed fact.
- Separate what the data shows from what you infer. State assumptions and confidence. Analysis results \
are planning estimates from terrain and reported data; say so when it matters, and say what ground \
truth would confirm them.
- If a tool fails, read the error, fix the call or take another route. If something is genuinely \
unavailable (no elevation loaded, Earth Engine not configured), do the next best thing and say what is \
missing.
- Prefer dedicated tools; fall back to search_gis_tools / run_gis_tool for anything else GIS can do, \
and to run_pyqgis only as a last resort.

Reporting: lead with the bottom line in one or two sentences, then the supporting points. Be brief - \
the map carries the detail. No filler.
"""


def build_system_prompt():
    now = datetime.now(timezone.utc)
    return SYSTEM_PROMPT + f"\nCurrent time: {now.strftime('%d%H%MZ %b %Y').upper()} ({now.date().isoformat()})."


class Agent:
    def __init__(self, toolbox, client=None, invoke=None, on_event=None):
        self.toolbox = toolbox
        self._client = client
        self.invoke = invoke or (lambda fn: fn())
        self.on_event = on_event or (lambda kind, data: None)
        self._abort = False

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(build_system_prompt(), self.toolbox.specs())
        return self._client

    def reset(self):
        if self._client is not None:
            self._client.reset()
        self._client = None

    def abort(self):
        self._abort = True

    def run(self, text, image_b64=None):
        """Handle one user request. Returns the final text."""
        self._abort = False
        emit = self.on_event
        try:
            emit("status", "Thinking")
            reply = self.client.send_user(text, image_b64)
            steps = 0
            while reply.tool_calls and not self._abort:
                steps += 1
                if steps > MAX_STEPS:
                    emit("error", f"Stopped after {MAX_STEPS} tool rounds without finishing.")
                    return ""
                if reply.text.strip():
                    emit("note", reply.text.strip())
                results = []
                for call in reply.tool_calls:
                    if self._abort:
                        results.append((call.id, call.name, "Cancelled by the operator.", True))
                        continue
                    emit("tool", {"name": call.name, "args": call.args})
                    out, is_err = self.invoke(lambda c=call: self.toolbox.call(c.name, c.args))
                    emit("tool_result", {"name": call.name, "ok": not is_err, "text": out})
                    results.append((call.id, call.name, out, is_err))
                emit("status", "Thinking")
                reply = self.client.send_tool_results(results)
            if self._abort:
                emit("error", "Stopped.")
                return ""
            if reply.stop == "max_tokens":
                reply.text += "\n\n[Response cut off at the token limit.]"
            emit("final", reply.text)
            return reply.text
        except LlmError as exc:
            emit("error", str(exc))
            return ""
        except Exception as exc:      # never let a worker thread die silently
            emit("error", f"{type(exc).__name__}: {exc}")
            return ""
