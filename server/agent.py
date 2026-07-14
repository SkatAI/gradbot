"""Persona → gradbot objects.

The only module besides `gradbot_session.py` that imports gradbot. Kept apart
from `personas.py` / `prompting.py` so those stay importable (and testable)
without the native extension, which has no macOS x86_64 wheel.
"""

from __future__ import annotations

import json

import gradbot

from personas import LLM_PROVIDERS, Persona, PersonaError
from prompting import build_system_instruction
from settings import get_settings


def build_session_config(persona: Persona) -> gradbot.SessionConfig:
    """The per-session voice config: what to say, in what voice, in what language."""
    lang = gradbot.LANGUAGES[persona.lang]
    return gradbot.SessionConfig(
        voice_id=persona.voice_id,
        instructions=build_system_instruction(persona),
        language=lang,
        # Language-specific text normalization applied before TTS (numbers,
        # abbreviations). `Lang` is a native enum, not a Python one — read the
        # property, don't reach for `.value`.
        rewrite_rules=lang.rewrite_rules,
        assistant_speaks_first=persona.gradbot.assistant_speaks_first,
        silence_timeout_s=persona.gradbot.silence_timeout_s,
        flush_duration_s=persona.gradbot.flush_duration_s,
        padding_bonus=persona.gradbot.padding_bonus,
        # Gradium's own STT knobs (`delay_in_frames`, `temp`, …), which gradbot
        # forwards verbatim into the Setup message's `json_config` without knowing
        # what any of them mean. It wants a JSON string; `personas.py` validated
        # the dict, so this cannot be the malformed JSON that gradbot would
        # silently discard. `None` when empty, so we send nothing at all rather
        # than an empty object.
        stt_extra_config=json.dumps(persona.stt.extra) if persona.stt.extra else None,
        # No tools. Gradbot supports them; neither persona defines any.
        tools=[],
    )


def build_run_kwargs(persona: Persona) -> dict:
    """Client kwargs for `gradbot.run()` — the speech and LLM endpoints.

    Unlike `SessionConfig` (which gradbot lets you swap mid-session), these are
    fixed when the session starts. Resolving them per-persona is what makes the
    LLM swappable at all: gradbot's own `config.from_env()` would pin one model
    process-wide.
    """
    settings = get_settings()
    if not settings.gradium_api_key:
        raise PersonaError("GRADIUM_API_KEY is not set — gradium powers both STT and TTS")

    api_key = getattr(settings, LLM_PROVIDERS[persona.llm.provider]["settings_key"])
    if not api_key:
        raise PersonaError(
            f"persona {persona.id!r}: no API key for llm.provider "
            f"{persona.llm.provider!r} — set it in server/.env"
        )

    kwargs = {
        "gradium_api_key": settings.gradium_api_key,
        "llm_api_key": api_key,
        "llm_model_name": persona.llm.model,
    }
    # gradbot fills in its own defaults for these when absent, so only pass them
    # when the persona/env actually override.
    if persona.llm.base_url:
        kwargs["llm_base_url"] = persona.llm.base_url
    if settings.gradium_base_url:
        kwargs["gradium_base_url"] = settings.gradium_base_url
    return kwargs
