"""Session routes: reserve a slot over HTTP, then run the call over a WebSocket.

A gradbot session has no room to join and no bot process to spawn: the session
*is* the WebSocket. So the work splits in two.

    POST /start-session  -> auth, persona, capacity check; reserves a session id
    WS   /ws/chat        -> browser sends {type:'start', session_id, access_token}
                            we re-verify, open the Supabase session row, and run
                            the traced gradbot bridge until someone hangs up.

The WebSocket re-verifies the JWT rather than trusting the earlier POST: a socket
is a separate connection and carries none of the headers we authorized before.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from loguru import logger
from pydantic import BaseModel

import session_tasks
from agent import build_run_kwargs, build_session_config
from auth import CurrentUser, get_current_user, user_from_token
from deps import get_pool
from gradbot_session import SessionPlan, handle_session
from personas import Persona, PersonaError
from recorder import SessionRecorder
from settings import get_settings
from tracing import SessionTracer

router = APIRouter()


class StartSessionBody(BaseModel):
    agent: str


def _load_persona(agent_name: str) -> Persona:
    """Load a persona by name, or raise 404. Shared by the POST and the socket."""
    agent_name = (agent_name or "").strip()
    if not agent_name:
        raise HTTPException(status_code=400, detail="agent_required")
    persona_path = get_settings().personas_dir / f"{agent_name}.json"
    if not persona_path.exists():
        raise HTTPException(status_code=404, detail="agent_not_found")
    try:
        with persona_path.open() as f:
            return Persona.from_dict(json.load(f), id=agent_name)
    except (OSError, json.JSONDecodeError, PersonaError) as e:
        logger.warning(f"Persona {agent_name!r} failed to load: {e}")
        raise HTTPException(status_code=404, detail="agent_not_found")


@router.post("/start-session")
async def start_session(
    body: StartSessionBody,
    user: CurrentUser = Depends(get_current_user),
):
    persona = _load_persona(body.agent)
    if persona.visibility == "admin" and not user.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")
    if session_tasks.count() >= get_settings().max_sessions:
        raise HTTPException(status_code=429, detail="agent_busy")

    reservation = session_tasks.reserve(agent=persona.id, user_id=user.id)
    logger.info(
        f"Reserved session {reservation.session_id} for {persona.id} "
        f"(slots={session_tasks.count()})"
    )
    # ws_url is relative: the browser resolves it against its own origin, so this
    # works unchanged as wss:// behind TLS and ws:// locally.
    return {"session_id": str(reservation.session_id), "ws_url": "/ws/chat"}


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, pool=Depends(get_pool)):
    # Assigned inside on_start, but torn down in the finally below — a call can
    # drop at any point after the session row exists.
    recorder: SessionRecorder | None = None
    tracer: SessionTracer | None = None
    session_id: str | None = None

    async def on_start(msg: dict) -> SessionPlan:
        """Authorize the socket, open the session row, and build the voice config.

        Every failure raises RuntimeError, which the bridge turns into a 4001
        close — a WebSocket has no HTTP status to answer with.
        """
        nonlocal recorder, tracer, session_id

        claimed = str(msg.get("session_id") or "")
        reservation = session_tasks.claim(claimed)
        if reservation is None:
            raise RuntimeError("unknown_or_expired_session")
        session_id = claimed

        token = str(msg.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("missing_access_token")
        try:
            user = await user_from_token(pool, token)
        except HTTPException as e:
            raise RuntimeError(f"auth_failed: {e.detail}") from e
        # The slot was reserved for one user; the token must be that same user's.
        if user.id != reservation.user_id:
            raise RuntimeError("session_user_mismatch")

        try:
            persona = _load_persona(reservation.agent)
        except HTTPException as e:
            raise RuntimeError(f"agent_unavailable: {e.detail}") from e

        try:
            plan = SessionPlan(
                config=build_session_config(persona),
                run_kwargs=build_run_kwargs(persona),
            )
        except PersonaError as e:
            raise RuntimeError(str(e)) from e

        # Reuse the reserved id so the row matches what /start-session already
        # handed the browser.
        recorder = SessionRecorder(
            pool, persona, user_id=user.id, session_id=reservation.session_id
        )
        await recorder.start()
        tracer = SessionTracer(recorder, lang=persona.lang)
        logger.info(f"Session {session_id} live: {persona.id} / {persona.llm.model}")
        return plan

    try:
        await handle_session(
            websocket,
            on_start=on_start,
            on_msg=lambda m: tracer.on_msg(m) if tracer is not None else None,
        )
    finally:
        if tracer is not None:
            tracer.close()  # a half-finished sentence was still said
        if recorder is not None:
            await recorder.close()
        if session_id is not None:
            session_tasks.release(session_id)
        logger.info(f"Session {session_id} ended")
