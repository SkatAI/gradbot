"""Project a stored persona snapshot into the session-detail view.

A session row keeps `persona_json` as a point-in-time snapshot of the persona it
ran. It may be a nested dict (this app's schema), a JSON string, a foreign or
legacy dict, or absent. `project_persona_snapshot` normalizes all of those into
the small view the detail page renders (or None).

"Foreign" matters here: this dashboard reads a database shared with sceance, so
it will be handed Pipecat persona snapshots too — Cartesia voices, Anthropic
models, a `pipecat` section. Those fail `Persona.from_dict` (this app's schema
only admits Gradium + OpenAI-compatible), and the pass-through branch below is
what keeps them renderable instead of 500-ing the page.
"""

from __future__ import annotations

import json

from personas import Persona, PersonaError


def project_persona_snapshot(persona_json) -> dict | None:
    """Return the detail-page persona view, or None when there's nothing usable.

    - dict  → projected through `Persona.from_dict` (nested schema)
    - str   → JSON-decoded first, then as above
    - legacy/flat dict that won't parse → returned as-is
    - None / non-dict → None
    """
    persona = persona_json
    if isinstance(persona, str):
        try:
            persona = json.loads(persona)
        except json.JSONDecodeError:
            return None
    if not isinstance(persona, dict):
        return None
    try:
        p = Persona.from_dict(persona)
    except PersonaError:
        # A sceance snapshot, or a legacy/flat one. Hand it back untouched — the
        # detail page renders a dict of whatever it's given.
        return persona
    return {
        "name": p.name,
        "lang": p.lang,
        "llm_model": p.llm.model,
        "llm": p.llm.description,
        "llm_provider": p.llm.provider,
        "tts_provider": p.tts.provider,
        "tts_voice_id": p.tts.voice_id,
        "voice_name": p.tts.voice_name,
        "voice_description": p.tts.voice_description,
        "description": p.description,
    }
