"""Persona schema + the two ported personas."""

from __future__ import annotations

import copy

import pytest

from personas import Persona, PersonaError, load_persona

PORTED = ("sophie_en", "leo_fr")


def minimal(**overrides) -> dict:
    raw = {
        "agent": {"active": True, "lang": "en", "visibility": "public"},
        "persona": {
            "name": "Test",
            "description": "d",
            "system_prompt": "be helpful",
            "greeting": "Hello.",
        },
        "llm": {"provider": "openai", "model": "gpt-4.1"},
        "tts": {"provider": "gradium", "voice_id": "abc123"},
        "stt": {"provider": "gradium"},
        "gradbot": {},
    }
    for section, patch in overrides.items():
        raw[section] = {**raw.get(section, {}), **patch}
    return raw


# ---- the two personas we actually ship -----------------------------------

@pytest.mark.parametrize("name", PORTED)
def test_ported_personas_load(name):
    persona = load_persona(name)
    assert persona.active
    assert persona.name
    assert persona.greeting
    assert persona.system_prompt  # baked in from system_prompt_path
    assert persona.voice_id


@pytest.mark.parametrize("name", PORTED)
def test_silence_timeout_is_zero(name):
    # gradbot defaults this to 5s, which makes the agent re-prompt itself with
    # its own last message whenever the user pauses — it talks to itself. Both
    # personas must pin it to 0.
    assert load_persona(name).gradbot.silence_timeout_s == 0.0


@pytest.mark.parametrize("name", PORTED)
def test_agent_opens_the_conversation(name):
    # There is no static-greeting path in gradbot; if the agent doesn't speak
    # first, nothing is said until the user says something.
    assert load_persona(name).gradbot.assistant_speaks_first is True


@pytest.mark.parametrize("name", PORTED)
def test_system_prompt_is_baked_into_the_snapshot(name):
    # sessions.persona_json must be self-contained — the .md file can change or
    # move, and the session row has to still explain what the agent was told.
    persona = load_persona(name)
    assert persona.raw["persona"]["system_prompt"] == persona.system_prompt


def test_the_two_personas_cover_both_languages():
    # The point of shipping exactly these two: they exercise the en and fr paths.
    personas = [load_persona(n) for n in PORTED]
    assert {p.lang for p in personas} == {"en", "fr"}


@pytest.mark.parametrize("name", PORTED)
def test_shipped_personas_run_on_openrouter(name):
    persona = load_persona(name)
    assert persona.llm.provider == "openrouter"
    assert persona.llm.model == "meta-llama/llama-4-maverick"
    assert persona.llm.base_url == "https://openrouter.ai/api/v1"


def test_the_openai_provider_means_openais_own_endpoint():
    # base_url None -> gradbot's default, which is api.openai.com. Not exercised by
    # the shipped personas, but the provider table offers it.
    persona = Persona.from_dict(minimal(llm={"provider": "openai", "model": "gpt-4.1"}), id="t")
    assert persona.llm.base_url is None


# ---- schema validation ----------------------------------------------------

def test_gradbot_defaults_silence_timeout_to_zero_not_five():
    persona = Persona.from_dict(minimal(), id="t")
    assert persona.gradbot.silence_timeout_s == 0.0


def test_non_openai_compatible_llm_is_rejected():
    # gradbot speaks the OpenAI wire protocol and nothing else. Anthropic's
    # native API would fail at call time; fail at load time instead.
    with pytest.raises(PersonaError, match="OpenAI-compatible"):
        Persona.from_dict(minimal(llm={"provider": "anthropic"}), id="t")


@pytest.mark.parametrize("provider", ["cartesia", "deepgram", "elevenlabs"])
def test_non_gradium_speech_providers_are_rejected(provider):
    with pytest.raises(PersonaError, match="gradium"):
        Persona.from_dict(minimal(tts={"provider": provider}), id="t")


def test_unsupported_language_is_rejected_rather_than_silently_englished():
    with pytest.raises(PersonaError, match="Lang enum"):
        Persona.from_dict(minimal(agent={"lang": "it"}), id="t")


def test_greeting_is_required():
    raw = minimal()
    del raw["persona"]["greeting"]
    with pytest.raises(PersonaError, match="greeting"):
        Persona.from_dict(raw, id="t")


def test_voice_id_is_required():
    raw = copy.deepcopy(minimal())
    del raw["tts"]["voice_id"]
    with pytest.raises(PersonaError, match="voice_id"):
        Persona.from_dict(raw, id="t")


def test_public_card_never_leaks_the_system_prompt():
    card = Persona.from_dict(minimal(), id="t").public()
    assert "be helpful" not in repr(card)
    assert card["memory"] is False  # this app has none
