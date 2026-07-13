"""Public, unauthenticated routes: config, health, agents."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from loguru import logger

import session_tasks
from auth import CurrentUser, get_current_user_optional
from personas import Persona, PersonaError
from settings import get_settings

router = APIRouter()


@router.get("/api/config")
async def api_config():
    """Public Supabase config consumed by the browser to initialize supabase-js."""
    settings = get_settings()
    return {
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "open_signup": settings.open_signup,
    }


@router.get("/api/audio-config")
async def audio_config():
    """Tells SyncedAudioPlayer which codec to expect from the server.

    We run gradbot's default (Ogg/Opus), not raw PCM — the browser decodes Opus
    with the worklet gradbot ships. Kept as an endpoint rather than a constant
    because the player fetches it on start, same as gradbot's own demos.
    """
    return {"pcm": False}


@router.get("/health")
async def health():
    return {"ok": True, "active_sessions": session_tasks.count()}


@router.get("/agents")
async def list_agents(user: CurrentUser | None = Depends(get_current_user_optional)):
    is_admin = bool(user and user.is_admin)
    agents = []
    for path in sorted(get_settings().personas_dir.glob("*.json")):
        try:
            with path.open() as f:
                data = json.load(f)
            persona = Persona.from_dict(data, id=path.stem)
        except (OSError, json.JSONDecodeError, PersonaError) as e:
            logger.warning(f"Skipping persona {path.name}: {e}")
            continue
        if not persona.active:
            continue
        if persona.visibility == "admin" and not is_admin:
            continue
        agents.append(persona.public())
    return {"agents": agents}
