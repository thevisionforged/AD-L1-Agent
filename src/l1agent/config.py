"""Runtime configuration. All values come from environment variables (see .env.example).

The OpenAI key is only ever read here, on the server side. It is never logged,
never returned by the API and never sent to the browser.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


PLACEHOLDER_KEYS = {"your_key_here", "your_api_key_here", "your_openai_api_key", "changeme", "placeholder"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip(), repr=False)
    openai_base_url: str | None = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL") or None)
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    embedding_model: str = field(default_factory=lambda: os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
    use_embeddings: bool = field(default_factory=lambda: os.getenv("USE_EMBEDDINGS", "true").lower() == "true")
    moderation_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest"))
    # "auto" = use OpenAI when a key is set, else offline rules; "online" / "offline" force a mode.
    agent_mode: str = field(default_factory=lambda: os.getenv("AGENT_MODE", "auto").lower())
    max_tool_calls: int = field(default_factory=lambda: _int("MAX_TOOL_CALLS", 6))
    max_output_tokens: int = field(default_factory=lambda: _int("MAX_OUTPUT_TOKENS", 800))
    request_timeout_s: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_SECONDS", 30.0))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    max_input_chars: int = field(default_factory=lambda: _int("MAX_INPUT_CHARS", 2000))
    max_turns_per_conversation: int = field(default_factory=lambda: _int("MAX_TURNS_PER_CONVERSATION", 20))
    # Kill switch: false = agents still read and propose, but NO approved action is executed (incident freeze).
    actions_enabled: bool = field(default_factory=lambda: os.getenv("ACTIONS_ENABLED", "true").lower() == "true")
    # demo = UI pickers (simulated SSO); easyauth = Azure App Service/Container Apps auth with Entra ID app roles
    auth_mode: str = field(default_factory=lambda: os.getenv("AUTH_MODE", "demo").lower())
    host: str = field(default_factory=lambda: os.getenv("APP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("APP_PORT", 8000))
    data_dir: Path = PROJECT_ROOT / "sample_data"
    runtime_dir: Path = field(default_factory=lambda: Path(os.getenv("RUNTIME_DIR", str(PROJECT_ROOT / "runtime"))))

    @property
    def online(self) -> bool:
        if self.agent_mode == "offline":
            return False
        key = self.openai_api_key.lower()
        return bool(key) and key not in PLACEHOLDER_KEYS and not key.startswith("your_")


def get_settings() -> Settings:
    return Settings()
