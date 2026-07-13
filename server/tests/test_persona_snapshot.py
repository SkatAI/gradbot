"""Rendering `sessions.persona_json` on the detail page.

The shared database is the whole reason this has teeth: this dashboard is handed
Pipecat snapshots as well as its own, and they do not parse under this app's
schema.
"""

from __future__ import annotations

import json

from persona_snapshot import project_persona_snapshot

GRADBOT_SNAPSHOT = {
    "agent": {"active": True, "lang": "fr", "visibility": "public"},
    "persona": {
        "name": "Leo",
        "description": "un guide",
        "system_prompt": "…",
        "greeting": "Bonjour.",
    },
    "llm": {"provider": "openai", "model": "gpt-4.1", "description": "GPT-4.1"},
    "tts": {"provider": "gradium", "voice_id": "axlOaUiFyOZhy4nv", "voice_name": "Leo"},
}

# What sceance writes: Cartesia voice, Anthropic model, a `pipecat` section.
# None of that is legal under this app's persona schema.
PIPECAT_SNAPSHOT = {
    "agent": {"active": True, "lang": "fr", "memory": False, "visibility": "public"},
    "persona": {"name": "Leo", "system_prompt": "…", "static_greeting": "Bonjour."},
    "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
    "tts": {"provider": "cartesia", "voice_id": "7345dfa5-ee04-44d2-abf4-29262b880ab4"},
    "pipecat": {"TextAggregationMode": "SENTENCE"},
}


def test_projects_our_own_snapshot():
    view = project_persona_snapshot(GRADBOT_SNAPSHOT)
    assert view["name"] == "Leo"
    assert view["llm_model"] == "gpt-4.1"
    assert view["tts_voice_id"] == "axlOaUiFyOZhy4nv"
    assert view["voice_name"] == "Leo"


def test_accepts_a_json_string():
    view = project_persona_snapshot(json.dumps(GRADBOT_SNAPSHOT))
    assert view["llm_model"] == "gpt-4.1"


def test_a_pipecat_snapshot_renders_instead_of_exploding():
    # Shared DB: the ledger lists sceance's calls too, and clicking one must not
    # 500 just because its persona uses providers this app rejects.
    view = project_persona_snapshot(PIPECAT_SNAPSHOT)
    assert view is not None
    assert view["llm"]["model"] == "claude-haiku-4-5"


def test_missing_snapshot_is_none():
    assert project_persona_snapshot(None) is None
    assert project_persona_snapshot("not json") is None
