"""Typed persona loader.

Persona JSON is nested by concern:

    {
      "agent":   { "active": bool, "lang": "en" | "fr", "visibility": "public" | "admin" },
      "persona": { "name", "description",
                   "system_prompt" | "system_prompt_path",   # inline OR a .md file
                   "greeting" },
      "llm":     { "provider", "model", "base_url"?, "description"? },
      "tts":     { "provider", "voice_id", "voice_name"?, "voice_description"? },
      "stt":     { "provider" },
      "gradbot": { "silence_timeout_s"?, "flush_duration_s"?,
                   "padding_bonus"?, "assistant_speaks_first"? }
    }

This is the single parse point: `load_persona` reads a file and returns a
`Persona`; everything else reads typed attributes off it. The original dict is
kept on `Persona.raw` for the DB snapshot.

The schema is deliberately narrow, and every omission is forced by gradbot:

- No `opening_messages` and no `static_greeting`. Gradbot has no API to seed
  context messages or to speak a fixed line — `SessionInputHandle` is only
  send_audio / send_config / close. The opening turn is LLM-generated, so the
  greeting is folded into the system instruction instead (see `prompting.py`).
- No `agent.memory`. Cross-session memory doesn't exist in this app.
- No TTS speed or STT model knobs. Gradbot exposes neither.
- `llm.provider` must be OpenAI-compatible; it resolves to an api-key + base-url
  pair (see `LLM_PROVIDERS`), because gradbot speaks nothing else.

This module deliberately does not import `gradbot` — that keeps persona parsing
testable outside the container (gradbot has no macOS x86_64 wheel). The mapping
onto `gradbot.SessionConfig` lives in `agent.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PERSONAS_DIR = Path(__file__).parent.parent / "personas"

# Gradbot talks to one LLM endpoint over the OpenAI wire protocol. A provider is
# therefore just "which api key, and which base url" — `base_url: None` means the
# provider is OpenAI itself, which is gradbot's default endpoint.
#
# `settings_key` names the attribute on `Settings` holding the API key.
LLM_PROVIDERS: dict[str, dict] = {
    "openai": {"settings_key": "openai_api_key", "base_url": None},
    "openrouter": {
        "settings_key": "openrouter_api_key",
        "base_url": "https://openrouter.ai/api/v1",
    },
}

# Gradbot's `Lang` enum covers exactly these; anything else is a config error
# rather than a silent fallback to English.
SUPPORTED_LANGS = ("en", "fr", "de", "es", "pt")


class PersonaError(ValueError):
    """Raised when a persona file is missing required fields or is malformed."""


def _read_prompt_file(rel_or_abs: str, persona_id: str) -> str:
    """Read a system-prompt markdown file. Relative paths resolve from the repo
    root (PERSONAS_DIR.parent), matching e.g. 'personas/systemprompts/x.md'."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = PERSONAS_DIR.parent / p
    if not p.exists():
        raise PersonaError(f"persona {persona_id!r}: system_prompt_path not found: {p}")
    return p.read_text().strip()


def _required(sec: dict, sec_name: str, key: str, id: str):
    if key not in sec or sec[key] in (None, ""):
        raise PersonaError(f"persona {id!r}: '{sec_name}.{key}' is required")
    return sec[key]


def _number(value, default: float, sec_name: str, key: str, id: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise PersonaError(f"persona {id!r}: '{sec_name}.{key}' must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise PersonaError(f"persona {id!r}: '{sec_name}.{key}' must be a number, got {value!r}")


@dataclass(frozen=True)
class AgentConfig:
    active: bool
    lang: str
    visibility: str  # "public" (everyone) | "admin" (admin users only)

    @classmethod
    def from_section(cls, agent: dict, id: str) -> "AgentConfig":
        visibility = str(agent.get("visibility") or "public").lower()
        if visibility not in ("public", "admin"):
            raise PersonaError(
                f"persona {id!r}: 'agent.visibility' must be 'public' or "
                f"'admin', got {visibility!r}"
            )
        lang = str(agent.get("lang") or "en").lower()
        if lang not in SUPPORTED_LANGS:
            raise PersonaError(
                f"persona {id!r}: 'agent.lang' must be one of "
                f"{', '.join(SUPPORTED_LANGS)} (gradbot's Lang enum), got {lang!r}"
            )
        return cls(
            active=bool(agent.get("active", False)),
            lang=lang,
            visibility=visibility,
        )


@dataclass(frozen=True)
class PersonaTextConfig:
    name: str
    description: str
    system_prompt: str
    greeting: str

    @classmethod
    def from_section(cls, persona: dict, id: str) -> "PersonaTextConfig":
        name = _required(persona, "persona", "name", id)
        # Inline `system_prompt` wins; otherwise load the markdown file named by
        # `system_prompt_path` and bake the resolved text back into the dict, so
        # the DB snapshot (persona.raw) is self-contained.
        system_prompt = persona.get("system_prompt") or ""
        if not system_prompt and persona.get("system_prompt_path"):
            system_prompt = _read_prompt_file(persona["system_prompt_path"], id)
            persona["system_prompt"] = system_prompt
        if not system_prompt:
            raise PersonaError(
                f"persona {id!r}: one of 'persona.system_prompt' or "
                f"'persona.system_prompt_path' is required"
            )
        return cls(
            name=name,
            description=persona.get("description", ""),
            system_prompt=system_prompt,
            # Required, not optional: the agent always opens the conversation
            # (`assistant_speaks_first`), so without this there is nothing to
            # tell it to say.
            greeting=_required(persona, "persona", "greeting", id),
        )


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str | None
    description: str

    @classmethod
    def from_section(cls, llm: dict, id: str) -> "LLMConfig":
        provider = str(_required(llm, "llm", "provider", id)).lower()
        if provider not in LLM_PROVIDERS:
            raise PersonaError(
                f"persona {id!r}: 'llm.provider' must be OpenAI-compatible — one of "
                f"{', '.join(LLM_PROVIDERS)}, got {provider!r}"
            )
        # An explicit base_url overrides the provider default (e.g. a local
        # Ollama/LM Studio endpoint under provider 'openai').
        base_url = llm.get("base_url") or LLM_PROVIDERS[provider]["base_url"]
        return cls(
            provider=provider,
            model=_required(llm, "llm", "model", id),
            base_url=base_url,
            description=llm.get("description", ""),
        )


@dataclass(frozen=True)
class TTSConfig:
    provider: str
    voice_id: str
    voice_name: str
    voice_description: str

    @classmethod
    def from_section(cls, tts: dict, id: str) -> "TTSConfig":
        provider = str(tts.get("provider") or "gradium").lower()
        if provider != "gradium":
            raise PersonaError(
                f"persona {id!r}: 'tts.provider' must be 'gradium' — gradbot has "
                f"no other TTS backend, got {provider!r}"
            )
        return cls(
            provider=provider,
            voice_id=_required(tts, "tts", "voice_id", id),
            voice_name=tts.get("voice_name", ""),
            voice_description=tts.get("voice_description", ""),
        )


@dataclass(frozen=True)
class STTConfig:
    provider: str

    @classmethod
    def from_section(cls, stt: dict, id: str) -> "STTConfig":
        provider = str(stt.get("provider") or "gradium").lower()
        if provider != "gradium":
            raise PersonaError(
                f"persona {id!r}: 'stt.provider' must be 'gradium' — gradbot has "
                f"no other STT backend, got {provider!r}"
            )
        return cls(provider=provider)


@dataclass(frozen=True)
class GradbotConfig:
    """Knobs on gradbot's Rust multiplexer, passed through to `SessionConfig`."""

    silence_timeout_s: float
    flush_duration_s: float
    padding_bonus: float
    assistant_speaks_first: bool

    @classmethod
    def from_section(cls, gb: dict, id: str) -> "GradbotConfig":
        # Default to 0.0, NOT gradbot's own 5.0.
        #
        # After `silence_timeout_s` of quiet, the Rust multiplexer injects the
        # literal string "..." into the conversation *as a user turn* and runs a
        # full LLM -> TTS cycle on it. The model, handed a contentless user turn,
        # fills the silence: it re-asks its last question or elaborates on its last
        # point. That is the "agent talks to itself" behaviour — it is answering a
        # prompt nobody spoke. (Capped at 5 nudges; reset by real speech.)
        #
        # Reasonable for a shopkeeper NPC. Wrong for a discernment guide, where
        # silence is the point. It also writes a spontaneous assistant turn with no
        # preceding `user_stopped_speaking`, so nothing pairs and turn counts drift.
        # 0.0 disables the branch outright (it is guarded by `> 0.0`).
        silence_timeout_s = _number(gb.get("silence_timeout_s"), 0.0, "gradbot",
                                    "silence_timeout_s", id)
        # How much silence ends the user's turn — and therefore when
        # `user_stopped_speaking` fires, which starts the response-latency
        # stopwatch. Default 0.2, not gradbot's own 0.5: this is the value most
        # VAD-based stacks use, so traces stay comparable with them. Raise it and
        # the agent waits longer before answering; every latency figure grows with
        # it, which is easy to mistake for the pipeline getting slower.
        return cls(
            silence_timeout_s=silence_timeout_s,
            flush_duration_s=_number(gb.get("flush_duration_s"), 0.2, "gradbot",
                                     "flush_duration_s", id),
            padding_bonus=_number(gb.get("padding_bonus"), 0.0, "gradbot",
                                  "padding_bonus", id),
            assistant_speaks_first=bool(gb.get("assistant_speaks_first", True)),
        )


@dataclass
class Persona:
    id: str
    raw: dict
    agent: AgentConfig
    text: PersonaTextConfig
    llm: LLMConfig
    tts: TTSConfig
    stt: STTConfig
    gradbot: GradbotConfig

    # ---- Flat convenience properties ---------------------------------------
    @property
    def active(self) -> bool:
        return self.agent.active

    @property
    def lang(self) -> str:
        return self.agent.lang

    @property
    def visibility(self) -> str:
        return self.agent.visibility

    @property
    def name(self) -> str:
        return self.text.name

    @property
    def description(self) -> str:
        return self.text.description

    @property
    def system_prompt(self) -> str:
        return self.text.system_prompt

    @property
    def greeting(self) -> str:
        return self.text.greeting

    @property
    def voice_id(self) -> str:
        return self.tts.voice_id

    @classmethod
    def from_dict(cls, raw: dict, id: str = "") -> "Persona":
        """Parse the nested schema. Raises PersonaError on a missing section or
        required field."""
        if not isinstance(raw, dict):
            raise PersonaError(f"persona {id!r}: expected a JSON object")

        def section(key: str) -> dict:
            val = raw.get(key)
            if not isinstance(val, dict):
                raise PersonaError(f"persona {id!r}: missing or invalid '{key}' section")
            return val

        def optional_section(key: str) -> dict:
            val = raw.get(key)
            return val if isinstance(val, dict) else {}

        return cls(
            id=id,
            raw=raw,
            agent=AgentConfig.from_section(section("agent"), id),
            text=PersonaTextConfig.from_section(section("persona"), id),
            llm=LLMConfig.from_section(section("llm"), id),
            tts=TTSConfig.from_section(section("tts"), id),
            stt=STTConfig.from_section(optional_section("stt"), id),
            gradbot=GradbotConfig.from_section(optional_section("gradbot"), id),
        )

    def public(self) -> dict:
        """The safe subset rendered on the /agents card."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "lang": self.lang,
            # The agent card in the browser reads this;
            # this app has no memory, so it is always false.
            "memory": False,
            "voice_name": self.tts.voice_name,
            "voice_description": self.tts.voice_description,
            "llm": self.llm.description,
            "tts_provider": self.tts.provider,
        }


def load_persona(agent_name: str) -> Persona:
    agent_name = (agent_name or "").strip()
    if not agent_name:
        raise PersonaError("agent_name is required")
    persona_path = PERSONAS_DIR / f"{agent_name}.json"
    if not persona_path.exists():
        raise FileNotFoundError(f"Persona file not found: {persona_path}")
    with persona_path.open() as f:
        raw = json.load(f)
    return Persona.from_dict(raw, id=agent_name)
