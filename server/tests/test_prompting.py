"""Assembly of the single `instructions` string gradbot gives us."""

from __future__ import annotations

from personas import Persona, load_persona
from prompting import build_system_instruction


def test_greeting_is_carried_into_the_instructions():
    # Gradbot has no way to seed context messages or speak a canned line, so the
    # opening has to ride inside the system prompt or it doesn't happen at all.
    persona = load_persona("yarden_mini")
    instruction = build_system_instruction(persona)
    assert persona.greeting in instruction
    assert "You speak first" in instruction


def test_persona_prompt_comes_first():
    persona = load_persona("yarden_mini")
    instruction = build_system_instruction(persona)
    assert instruction.startswith(persona.system_prompt)


def test_language_is_pinned_to_the_personas_language():
    fr = build_system_instruction(load_persona("inigo_v5_fr"))
    assert "Always speak French" in fr

    en = build_system_instruction(load_persona("yarden_mini"))
    assert "Always speak English" in en


def test_no_memory_brief_promise():
    # This app has no cross-session memory. Promising the agent a "memory brief"
    # it will never receive just makes it talk about context it doesn't have.
    instruction = build_system_instruction(load_persona("yarden_mini"))
    assert "memory brief" not in instruction.lower()


def test_voice_rules_forbid_unspeakable_formatting():
    instruction = build_system_instruction(load_persona("yarden_mini"))
    assert "markdown" in instruction.lower()


def test_instruction_is_a_plain_string_for_session_config():
    raw = {
        "agent": {"active": True, "lang": "fr", "visibility": "public"},
        "persona": {"name": "T", "system_prompt": "x", "greeting": "Bonjour."},
        "llm": {"provider": "openai", "model": "gpt-4.1"},
        "tts": {"provider": "gradium", "voice_id": "v"},
    }
    instruction = build_system_instruction(Persona.from_dict(raw, id="t"))
    assert isinstance(instruction, str)
    assert "Bonjour." in instruction
