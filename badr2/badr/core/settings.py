"""Persistent settings (QSettings-backed, falls back to a dict without Qt)."""
import json
import os

try:
    from qgis.PyQt.QtCore import QSettings
except Exception:  # pragma: no cover - pure-python contexts
    QSettings = None

ORG, APP = "BADR", "BADR2"

# provider id -> wire format + defaults.  Two wire formats cover everything:
# "anthropic" (Messages API) and "openai" (chat/completions, which is also what
# Gemini's compatibility endpoint, Ollama, LM Studio and vLLM speak).  Any
# OpenAI-compatible endpoint works, so the app can run fully air-gapped
# against a local model.
PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "wire": "anthropic",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
        "needs_key": True,
    },
    "openai": {
        "label": "OpenAI",
        "wire": "openai",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "needs_key": True,
    },
    "gemini": {
        "label": "Google Gemini",
        "wire": "openai",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
        "needs_key": True,
    },
    "ollama": {
        "label": "Ollama (local / air-gapped)",
        "wire": "openai",
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "models": ["llama3.1", "qwen2.5"],
        "needs_key": False,
    },
    "custom": {
        "label": "OpenAI-compatible endpoint (on-prem)",
        "wire": "openai",
        "endpoint": "http://localhost:8080/v1/chat/completions",
        "models": ["default"],
        "needs_key": False,
    },
}

DEFAULTS = {
    "ai/provider": "anthropic",
    "ai/max_tokens": 4096,
    "ai/effort": "low",                # low = fast (map actions), medium / high = deeper analysis
    "ai/review_ai_changes": True,      # AI-created features land as "pending review"
    "ai/confirm_code": True,           # ask before running AI-written PyQGIS
    "gee/project": "",
    "ui/classification": "UNCLASSIFIED // TRAINING",
    "ui/coord_format": "MGRS",
    "ui/symbol_scale": 1.0,
    "workspace": os.path.join(os.path.expanduser("~"), "BADR", "operations"),
}

_ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


class Settings:
    _mem = {}

    @classmethod
    def _qs(cls):
        return QSettings(ORG, APP) if QSettings else None

    @classmethod
    def get(cls, key, default=None):
        if default is None:
            default = DEFAULTS.get(key)
        qs = cls._qs()
        if qs is None:
            return cls._mem.get(key, default)
        val = qs.value(key, None)
        if val is None:
            return default
        if isinstance(default, bool):
            return str(val).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            return int(val)
        if isinstance(default, float):
            return float(val)
        return val

    @classmethod
    def set(cls, key, value):
        qs = cls._qs()
        if qs is None:
            cls._mem[key] = value
        else:
            qs.setValue(key, value)

    # ---- AI convenience -------------------------------------------------
    @classmethod
    def provider(cls):
        p = cls.get("ai/provider")
        return p if p in PROVIDERS else "anthropic"

    @classmethod
    def api_key(cls, provider=None):
        provider = provider or cls.provider()
        key = cls.get(f"ai/{provider}/key", "") or ""
        if not key and provider in _ENV_KEYS:
            key = os.environ.get(_ENV_KEYS[provider], "")
        return key

    @classmethod
    def model(cls, provider=None):
        provider = provider or cls.provider()
        return cls.get(f"ai/{provider}/model", "") or PROVIDERS[provider]["models"][0]

    @classmethod
    def endpoint(cls, provider=None):
        provider = provider or cls.provider()
        return cls.get(f"ai/{provider}/endpoint", "") or PROVIDERS[provider]["endpoint"]

    @classmethod
    def ai_ready(cls):
        p = cls.provider()
        return bool(cls.api_key(p)) or not PROVIDERS[p]["needs_key"]

    # ---- sensor connection profiles -------------------------------------
    @classmethod
    def connections(cls):
        try:
            return json.loads(cls.get("sensors/connections", "[]") or "[]")
        except ValueError:
            return []

    @classmethod
    def set_connections(cls, conns):
        cls.set("sensors/connections", json.dumps(conns))
