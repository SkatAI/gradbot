"""Centralized application settings.

One `Settings` object holds the environment-derived configuration. Handlers and
services read it via `get_settings()` at call time, so values aren't frozen at
import and tests can override the whole object in one place (`set_settings`) or
patch a single attribute on `get_settings()`.

Trimmed from the sceance original: this app has no Daily transport and only ever
talks to Gradium (STT + TTS) and an OpenAI-compatible LLM endpoint, so the
Daily/Deepgram/Cartesia/Anthropic/Groq keys are gone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_SERVER_DIR = Path(__file__).parent
_DEFAULT_STATIC_DIR = _SERVER_DIR / "static"
_DEFAULT_PERSONAS_DIR = _SERVER_DIR.parent / "personas"


@dataclass
class Settings:
    # Session limits
    max_sessions: int = 5
    # When true, an unrecognized email is auto-created on sign-in (local/dev).
    open_signup: bool = False
    # Where the server runs: 'local' (dev .env) or 'online' (deployed secret);
    # 'unknown' when unset. Recorded on each session row.
    deploy_env: str = "unknown"
    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""
    supabase_pool_max: int = 8
    # Gradium powers both STT and TTS — the only speech provider in this app.
    gradium_api_key: str | None = None
    gradium_base_url: str | None = None
    # LLM keys, looked up by `llm.provider` in the persona (see personas.LLM_PROVIDERS).
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    # Not a persona provider: the model that writes the per-session latency
    # analysis on the dashboard. Read by latency_report.py via os.getenv.
    anthropic_api_key: str | None = None
    # Paths
    static_dir: Path = _DEFAULT_STATIC_DIR
    personas_dir: Path = _DEFAULT_PERSONAS_DIR

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(override=True)
        return cls(
            max_sessions=int(os.getenv("MAX_SESSIONS", "5")),
            open_signup=os.getenv("OPEN_SIGNUP", "").strip().lower() in ("1", "true", "yes"),
            deploy_env=os.getenv("DEPLOY_ENV", "unknown").strip().lower() or "unknown",
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            supabase_anon_key=os.getenv("SUPABASE_KEY", ""),
            supabase_jwt_secret=os.getenv("SUPABASE_JWT_SECRET", "").strip(),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            supabase_db_url=os.getenv("SUPABASE_DB_URL", "").strip(),
            supabase_pool_max=int(os.getenv("SUPABASE_POOL_MAX", "8")),
            gradium_api_key=os.getenv("GRADIUM_API_KEY"),
            gradium_base_url=os.getenv("GRADIUM_BASE_URL"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """The process-wide settings, built lazily from the environment on first use."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Override the settings singleton, or reset it (None) to re-read env next call."""
    global _settings
    _settings = settings
