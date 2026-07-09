"""Settings persistence using QSettings."""
from enum import Enum
from qgis.core import QgsSettings


class LlmProvider(Enum):
    Anthropic = "Anthropic"
    OpenAI = "OpenAI"
    GoogleGemini = "GoogleGemini"
    Ollama = "Ollama"
    OpenAICompatible = "OpenAICompatible"


PROVIDER_INFO = {
    LlmProvider.Anthropic: {
        "display": "Anthropic (Claude)",
        "models": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
        "endpoint": "https://api.anthropic.com/v1/messages",
        "needs_key": True,
        "key_help": "Get your key at console.anthropic.com/settings/keys",
    },
    LlmProvider.OpenAI: {
        "display": "OpenAI (GPT)",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "needs_key": True,
        "key_help": "Get your key at platform.openai.com/api-keys",
    },
    LlmProvider.GoogleGemini: {
        "display": "Google Gemini (free tier available)",
        "models": ["gemini-3.1-pro", "gemini-3.1-flash-lite", "gemini-2.0-flash", "gemini-2.0-pro"],
        "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        "needs_key": True,
        "key_help": "Get your FREE key at aistudio.google.com/apikey",
    },
    LlmProvider.Ollama: {
        "display": "Ollama (local, completely free)",
        "models": ["llama3.1", "mistral", "codellama", "deepseek-coder-v2"],
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "needs_key": False,
        "key_help": "No API key needed! Install Ollama from ollama.com",
    },
    LlmProvider.OpenAICompatible: {
        "display": "OpenAI-compatible (LM Studio, vLLM...)",
        "models": ["default"],
        "endpoint": "http://localhost:8080/v1/chat/completions",
        "needs_key": True,
        "key_help": "Enter the API key for your endpoint (if required)",
    },
}


class Settings:
    PREFIX = "BADR/"

    @staticmethod
    def _s():
        return QgsSettings()

    @classmethod
    def provider(cls) -> LlmProvider:
        val = cls._s().value(cls.PREFIX + "provider", "Anthropic")
        try:
            return LlmProvider(val)
        except ValueError:
            return LlmProvider.Anthropic

    @classmethod
    def set_provider(cls, p: LlmProvider):
        cls._s().setValue(cls.PREFIX + "provider", p.value)

    @classmethod
    def api_key(cls, provider: LlmProvider = None) -> str:
        if provider is None:
            provider = cls.provider()
        provider_key = cls.PREFIX + f"api_key_{provider.value}"
        if cls._s().contains(provider_key):
            return cls._s().value(provider_key, "")
        
        # Fallback to legacy shared key for migration
        return cls._s().value(cls.PREFIX + "api_key", "")

    @classmethod
    def set_api_key(cls, key: str, provider: LlmProvider = None):
        if provider is None:
            provider = cls.provider()
        cls._s().setValue(cls.PREFIX + f"api_key_{provider.value}", key)

    @classmethod
    def model(cls, provider: LlmProvider = None) -> str:
        if provider is None:
            provider = cls.provider()
        val = cls._s().value(cls.PREFIX + f"model_{provider.value}", "")
        
        # Migrate deprecated Gemini model
        if provider == LlmProvider.GoogleGemini and val == "gemini-1.5-flash":
            val = "gemini-2.0-flash"
            cls.set_model(val, provider)
            
        if not val:
            # Fallback to legacy shared model only if valid for this provider
            legacy = cls._s().value(cls.PREFIX + "model", "")
            if legacy and legacy in PROVIDER_INFO[provider]["models"]:
                val = legacy
        
        if not val:
            # Default for provider
            return PROVIDER_INFO[provider]["models"][0]
        return val

    @classmethod
    def set_model(cls, m: str, provider: LlmProvider = None):
        if provider is None:
            provider = cls.provider()
        cls._s().setValue(cls.PREFIX + f"model_{provider.value}", m)

    @classmethod
    def endpoint(cls, provider: LlmProvider = None) -> str:
        if provider is None:
            provider = cls.provider()
        provider_key = cls.PREFIX + f"endpoint_{provider.value}"
        if cls._s().contains(provider_key):
            return cls._s().value(provider_key, "")
            
        # Only fallback to global endpoint for Ollama/Compatible
        if provider in (LlmProvider.Ollama, LlmProvider.OpenAICompatible):
            return cls._s().value(cls.PREFIX + "endpoint", "")
        return ""

    @classmethod
    def set_endpoint(cls, url: str, provider: LlmProvider = None):
        if provider is None:
            provider = cls.provider()
        cls._s().setValue(cls.PREFIX + f"endpoint_{provider.value}", url)

    @classmethod
    def effective_endpoint(cls, provider: LlmProvider = None) -> str:
        if provider is None:
            provider = cls.provider()
        ep = cls.endpoint(provider)
        if ep:
            return ep
        return PROVIDER_INFO[provider]["endpoint"]

    @classmethod
    def max_tokens(cls) -> int:
        return int(cls._s().value(cls.PREFIX + "max_tokens", 4096))

    @classmethod
    def set_max_tokens(cls, v: int):
        cls._s().setValue(cls.PREFIX + "max_tokens", v)

    @classmethod
    def confirm_before_execute(cls) -> bool:
        v = cls._s().value(cls.PREFIX + "confirm_before_execute", "true")
        if isinstance(v, bool): return v
        return str(v).lower() == "true"

    @classmethod
    def set_confirm_before_execute(cls, v: bool):
        cls._s().setValue(cls.PREFIX + "confirm_before_execute", "true" if v else "false")

    @classmethod
    def show_generated_code(cls) -> bool:
        v = cls._s().value(cls.PREFIX + "show_generated_code", "true")
        if isinstance(v, bool): return v
        return str(v).lower() == "true"

    @classmethod
    def set_show_generated_code(cls, v: bool):
        cls._s().setValue(cls.PREFIX + "show_generated_code", "true" if v else "false")

    @classmethod
    def gee_project(cls) -> str:
        return cls._s().value(cls.PREFIX + "gee_project", "")

    @classmethod
    def set_gee_project(cls, project: str):
        cls._s().setValue(cls.PREFIX + "gee_project", project)

    @classmethod
    def knowledge_base_dir(cls) -> str:
        import os
        default_dir = os.path.join(os.path.dirname(__file__), "knowledge_base")
        val = cls._s().value(cls.PREFIX + "knowledge_base_dir", default_dir)
        return val

    @classmethod
    def set_knowledge_base_dir(cls, path: str):
        cls._s().setValue(cls.PREFIX + "knowledge_base_dir", path)

    @classmethod
    def vector_db_path(cls) -> str:
        import os
        return os.path.join(cls.knowledge_base_dir(), "vector_db.json")

    @classmethod
    def vector_db_last_updated(cls) -> str:
        return cls._s().value(cls.PREFIX + "vector_db_last_updated", "Never")

    @classmethod
    def set_vector_db_last_updated(cls, timestamp: str):
        cls._s().setValue(cls.PREFIX + "vector_db_last_updated", timestamp)

    @classmethod
    def workspace_folder(cls) -> str:
        """Return the user-configured workspace folder for permanent layer storage."""
        return cls._s().value(cls.PREFIX + "workspace_folder", "")

    @classmethod
    def set_workspace_folder(cls, path: str):
        cls._s().setValue(cls.PREFIX + "workspace_folder", path)

