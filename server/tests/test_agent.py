"""Persona -> gradbot.SessionConfig.

The only test module that imports `agent`, and therefore the only one that needs
the native extension. Every other test stays importable on a host with no gradbot
wheel (see `personas.py`'s header); `importorskip` keeps it that way — these run
under `make test`, inside the container, and skip anywhere else.
"""

from __future__ import annotations

import json

import pytest

from personas import Persona

pytest.importorskip("gradbot", reason="native extension; runs inside the container")

from agent import build_session_config  # noqa: E402


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


def test_stt_extra_reaches_gradbot_as_a_json_string():
    # gradbot wants a string and parses it back itself. If we handed it anything
    # it couldn't parse as a JSON *object*, its Rust core would drop the config on
    # the floor without a word — so what leaves here must survive a round-trip.
    persona = Persona.from_dict(
        minimal(stt={"provider": "gradium", "extra": {"delay_in_frames": 24, "temp": 0.0}}),
        id="t",
    )
    cfg = build_session_config(persona)

    assert isinstance(cfg.stt_extra_config, str)
    assert json.loads(cfg.stt_extra_config) == {"delay_in_frames": 24, "temp": 0.0}


def test_no_stt_extra_means_we_send_none_rather_than_an_empty_object():
    cfg = build_session_config(Persona.from_dict(minimal(), id="t"))
    assert cfg.stt_extra_config is None


def test_the_flush_window_reaches_gradbot():
    # The knob that decides whether the agent hears the end of your sentence.
    persona = Persona.from_dict(minimal(gradbot={"flush_duration_s": 0.6}), id="t")
    assert build_session_config(persona).flush_duration_s == 0.6
