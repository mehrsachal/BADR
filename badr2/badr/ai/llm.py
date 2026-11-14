"""LLM clients behind one small interface.

Two wire formats cover every supported provider:

* ``AnthropicClient``  - Claude through the official ``anthropic`` SDK.
* ``OpenAICompatClient`` - the chat/completions format spoken by OpenAI,
  Gemini's compatibility endpoint, Ollama, LM Studio, vLLM ... which is what
  makes fully air-gapped, on-premises deployments possible.

Both expose ``send_user`` / ``send_tool_results`` and return a ``Reply`` with
text and structured tool calls; each keeps its own provider-native history.
"""
import json
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.settings import PROVIDERS, Settings

MAX_HISTORY = 60


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    stop: str = "end"            # end | tool_use | max_tokens | refusal


class LlmError(RuntimeError):
    pass


class BaseClient:
    def __init__(self, system, tools):
        self.system = system
        self.tools = tools          # [{name, description, parameters}]
        self.history = []

    def reset(self):
        self.history = []

    def send_user(self, text, image_b64=None):
        raise NotImplementedError

    def send_tool_results(self, results):
        """results: [(tool_call_id, tool_name, content_text, is_error)]"""
        raise NotImplementedError

    def _trim(self):
        if len(self.history) <= MAX_HISTORY:
            return
        cut = len(self.history) - MAX_HISTORY
        # never start the window on a tool result or an assistant turn
        while cut < len(self.history) and not self._is_plain_user(self.history[cut]):
            cut += 1
        self.history = self.history[cut:]

    @staticmethod
    def _is_plain_user(msg):
        if msg.get("role") != "user":
            return False
        c = msg.get("content")
        if isinstance(c, list):
            return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        return True


# ---------------------------------------------------------------------------
class AnthropicClient(BaseClient):
    def __init__(self, system, tools, api_key=None, model=None, max_tokens=None):
        super().__init__(system, tools)
        try:
            import anthropic
        except ImportError as exc:
            raise LlmError("The 'anthropic' package is not installed in this Python. "
                           "Run: python -m pip install anthropic") from exc
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or Settings.api_key("anthropic") or None)
        self.model = model or Settings.model("anthropic")
        self.max_tokens = int(max_tokens or max(int(Settings.get("ai/max_tokens")), 16000))
        self._fallbacks = True

    def _tools(self):
        return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in self.tools]

    def _call(self):
        self._trim()
        a = self._anthropic
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=self.system,
                      tools=self._tools(), messages=self.history)
        try:
            if self._fallbacks:
                try:
                    # On a safety-classifier decline, let the API re-run the
                    # request on its default fallback model inside the same call.
                    resp = self.client.beta.messages.create(
                        betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
                except (TypeError, a.BadRequestError):
                    self._fallbacks = False       # older SDK / platform without the feature
                    resp = self.client.messages.create(**kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as exc:
            raise LlmError("Anthropic rejected the API key. Check Settings > AI.") from exc
        except a.RateLimitError as exc:
            raise LlmError("Rate limited by the Anthropic API; wait a moment and retry.") from exc
        except a.APIStatusError as exc:
            raise LlmError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LlmError("Could not reach the Anthropic API (network).") from exc
        # keep the full content (including thinking blocks) for the next turn
        self.history.append({"role": "assistant", "content": resp.content})
        reply = Reply()
        for block in resp.content:
            if block.type == "text":
                reply.text += block.text
            elif block.type == "tool_use":
                reply.tool_calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
        reply.stop = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(
            resp.stop_reason, "end")
        if reply.stop == "refusal":
            reply.tool_calls = []
            reply.text = reply.text or "The model declined this request."
        return reply

    def send_user(self, text, image_b64=None):
        content = text
        if image_b64:
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": image_b64}},
                       {"type": "text", "text": text}]
        self.history.append({"role": "user", "content": content})
        return self._call()

    def send_tool_results(self, results):
        # all results for one assistant turn go back in a single user message
        self.history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": content, **({"is_error": True} if err else {})}
            for cid, _name, content, err in results]})
        return self._call()


# ---------------------------------------------------------------------------
class OpenAICompatClient(BaseClient):
    def __init__(self, system, tools, endpoint=None, api_key=None, model=None, max_tokens=None, provider=None):
        super().__init__(system, tools)
        provider = provider or Settings.provider()
        self.endpoint = endpoint or Settings.endpoint(provider)
        self.api_key = api_key if api_key is not None else Settings.api_key(provider)
        self.model = model or Settings.model(provider)
        self.max_tokens = int(max_tokens or Settings.get("ai/max_tokens"))

    def _post(self, body):
        data = json.dumps(body).encode("utf-8")
        for attempt in range(4):
            req = Request(self.endpoint, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            try:
                with urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code in (429, 503) and attempt < 3:
                    time.sleep(3 * 2 ** attempt)
                    continue
                detail = exc.read().decode("utf-8", "replace")[:500]
                raise LlmError(f"LLM endpoint error {exc.code}: {detail}") from exc
            except URLError as exc:
                raise LlmError(f"Could not reach the LLM endpoint {self.endpoint}: {exc.reason}") from exc
        raise LlmError("LLM endpoint kept rate-limiting the request.")

    def _call(self):
        self._trim()
        body = {
            "model": self.model, "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": self.system}] + self.history,
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in self.tools],
        }
        data = self._post(body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as exc:
            raise LlmError(f"Unexpected response from LLM endpoint: {str(data)[:300]}") from exc
        self.history.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        reply = Reply(text=msg.get("content") or "")
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_invalid_json": fn.get("arguments")}
            reply.tool_calls.append(ToolCall(tc.get("id") or fn.get("name", "call"), fn.get("name", ""), args))
        reply.stop = "tool_use" if reply.tool_calls else (
            "max_tokens" if choice.get("finish_reason") == "length" else "end")
        return reply

    def send_user(self, text, image_b64=None):
        content = text
        if image_b64:
            content = [{"type": "text", "text": text},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]
        self.history.append({"role": "user", "content": content})
        return self._call()

    def send_tool_results(self, results):
        for cid, _name, content, _err in results:
            self.history.append({"role": "tool", "tool_call_id": cid, "content": content})
        return self._call()


def make_client(system, tools):
    provider = Settings.provider()
    info = PROVIDERS[provider]
    if info["needs_key"] and not Settings.api_key(provider):
        raise LlmError(f"No API key set for {info['label']}. Open Settings > AI, or choose a local model "
                       "(Ollama / on-prem endpoint) for offline use.")
    if info["wire"] == "anthropic":
        return AnthropicClient(system, tools)
    return OpenAICompatClient(system, tools, provider=provider)
