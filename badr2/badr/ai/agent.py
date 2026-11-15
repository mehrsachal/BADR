"""The planning assistant: an LLM in a tool loop over the Toolbox.

Threading contract: ``run`` may be called from a worker thread.  LLM requests
happen on the calling thread; every tool call is passed to ``invoke``, which
the UI implements as "run on the GUI thread and wait" (QGIS objects are not
thread-safe).  Headless callers leave ``invoke`` as a direct call.
"""
from datetime import datetime, timezone

from .llm import LlmError, make_client

MAX_STEPS = 12

SYSTEM_PROMPT = """\
You are BADR, a planning and analysis assistant in a military planning GIS. Scope: planning and \
analysis only - sensor links are receive-only and a human decides and acts.

Act through tools, then answer with the brief tool - never with plain text.
- brief: 1-5 beats. Each beat is ONE short sentence (max 16 words): bottom line first, then the facts \
that matter. Give each beat an 'at' (place or uid) so the map can fly there. No preamble, no lists, \
no restating the question. Small talk: one beat.
- Call brief in the same turn as your last actions; do not wait for their results first.
- Locations: MGRS ('11S NU 5512 0345'), 'lat, lon', a feature uid or name, or 'view' (map centre).
- Affiliations: friend, hostile, neutral, unknown, suspect, assumed_friend, pending.
- Be economical: call get_picture only when the answer depends on what is on the map, once. Issue \
independent tool calls together. Never repeat a call.
- What you add is held for operator review; say so in one beat when relevant.
- Say what is data and what is inference; analysis results are planning estimates.
- If a tool fails, fix the call or say in one beat what is missing.
"""


def build_system_prompt():
    now = datetime.now(timezone.utc)
    # date only: a per-minute timestamp would change the prompt and defeat prompt caching
    return SYSTEM_PROMPT + f"\nToday is {now.strftime('%d %b %Y').upper()} ({now.date().isoformat()}), times are Zulu."


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
            usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}

            def count(r):
                usage["in"] += r.tokens_in
                usage["cached"] += r.tokens_cached
                usage["out"] += r.tokens_out
                usage["calls"] += 1
                return r
            reply = count(self.client.send_user(text, image_b64))
            steps = 0
            while reply.tool_calls and not self._abort:
                steps += 1
                if steps > MAX_STEPS:
                    emit("error", f"Stopped after {MAX_STEPS} tool rounds without finishing.")
                    return ""
                if reply.text.strip():
                    emit("note", reply.text.strip())
                results = []
                self.toolbox.last_brief = None
                for call in reply.tool_calls:
                    if self._abort:
                        results.append((call.id, call.name, "Cancelled by the operator.", True))
                        continue
                    if call.name != "brief":
                        emit("tool", {"name": call.name, "args": call.args})
                    out, is_err = self.invoke(lambda c=call: self.toolbox.call(c.name, c.args))
                    if call.name != "brief":
                        emit("tool_result", {"name": call.name, "ok": not is_err, "text": out})
                    results.append((call.id, call.name, out, is_err))
                story = getattr(self.toolbox, "last_brief", None)
                if story and not any(err for _i, name, _o, err in results if name != "brief"):
                    # The brief IS the answer.  Do not spend another model call just to
                    # acknowledge it: park the tool results until the next user turn.
                    self.client.defer_tool_results(results)
                    emit("usage", usage)
                    emit("story", story)
                    emit("final", "")
                    return " ".join(b["say"] for b in story)
                emit("status", "Thinking")
                reply = count(self.client.send_tool_results(results))
            if self._abort:
                emit("error", "Stopped.")
                return ""
            if reply.stop == "max_tokens":
                reply.text += "\n\n[Response cut off at the token limit.]"
            emit("usage", usage)
            emit("final", reply.text)
            return reply.text
        except LlmError as exc:
            emit("error", str(exc))
            return ""
        except Exception as exc:      # never let a worker thread die silently
            emit("error", f"{type(exc).__name__}: {exc}")
            return ""
