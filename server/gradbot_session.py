"""A traced fork of `gradbot.websocket.handle_session`.

WHY THIS EXISTS
---------------
Gradbot's own WebSocket bridge is a closed pipe: it reads `MsgOut` objects off
the Rust multiplexer, converts each to a wire schema, and forwards it to the
browser. It offers no hook to observe them. That's fine for a demo and fatal for
us — every transcript, every per-stage timing, everything this app needs to write
to Supabase, only exists inside that loop.

Worse, the conversion is lossy in exactly the place that matters:
`gradbot.schemas.SessionEvent` keeps `event` and drops `time_s`. So even a
browser-side tap would not recover the timings. The data has to be intercepted
*upstream* of `schemas.from_msg()`.

Hence this fork. Three deliberate divergences from upstream, and nothing else:

1. An `on_msg` callback, invoked on every `MsgOut` before conversion. This is the
   whole point.
2. `on_start` returns a `SessionPlan` (config + run kwargs) rather than a bare
   `SessionConfig`. Upstream takes the LLM endpoint from a process-wide `Config`,
   which would pin every persona to one model; we resolve it per session, and the
   persona is only known once the start message arrives.
3. The tool-call machinery is gone — no persona here defines tools.

KEEPING IT HONEST
-----------------
`gradbot` is pinned to `==0.1.10` in pyproject.toml precisely because this file
is a copy. If you unpin it, diff this against
`gradbot_py/gradbot/websocket.py` at the new tag first.

Everything else — the input loop, the start-message handshake, the error paths,
the close semantics — is upstream's, deliberately unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import fastapi

import gradbot
from gradbot import schemas

logger = logging.getLogger("gradbot_session")


@dataclass
class SessionPlan:
    """What `on_start` resolves from the client's start message.

    `config` is what to say and how to say it (swappable mid-session);
    `run_kwargs` is which endpoints to say it through (fixed for the session).
    """

    config: Any  # gradbot.SessionConfig
    run_kwargs: dict = field(default_factory=dict)


StartCallback = Callable[[dict], Awaitable[SessionPlan] | SessionPlan]
ConfigCallback = Callable[[dict], Awaitable[Any] | Any]
MsgCallback = Callable[[Any], None]


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    """Wrap a sync function to be awaitable."""
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


async def handle_session(
    websocket: fastapi.WebSocket,
    *,
    on_start: StartCallback,
    on_msg: MsgCallback | None = None,
    on_config: ConfigCallback | None = None,
    input_format=gradbot.AudioFormat.OggOpus,
    output_format=gradbot.AudioFormat.OggOpus,
    debug: bool = False,
) -> None:
    """Handle a full WebSocket voice-chat session.

    `on_start` receives the client's start message and returns a `SessionPlan`.
    Raise `RuntimeError` from it to reject the connection (bad auth, unknown
    agent, server at capacity) — the socket closes with code 4001.

    `on_msg`, if given, is called with every `MsgOut` the multiplexer emits,
    before it is converted for the wire. It must not block — `tracing.py` only
    enqueues.

    Protocol (unchanged from upstream):
    - Client sends JSON ``{"type": "start", ...}``.
    - Client sends binary frames with audio data.
    - Client sends JSON ``{"type": "config", ...}`` to reconfigure mid-session.
    - Client sends JSON ``{"type": "stop"}``.
    - Server sends transcripts, events, audio, timing.
    """
    on_start = _ensure_async(on_start)
    if on_config is not None:
        on_config = _ensure_async(on_config)

    async def send_error(exc: Exception) -> None:
        try:
            text = str(exc) if debug else "An error occurred"
            await websocket.send_json(schemas.Error(message=text).model_dump())
        except Exception:
            ...

    async def handle_input(raw: dict) -> bool:
        """Handle one input frame. Returns False to stop."""
        if "bytes" in raw:
            await input_handle.send_audio(raw["bytes"])
            return True

        if "text" not in raw:
            return True

        data = json.loads(raw["text"])
        msg_type = data.get("type")
        if msg_type == "stop":
            return False

        if msg_type == "config" and on_config is not None:
            try:
                new_cfg = await on_config(data)
                await input_handle.send_config(new_cfg)
            except RuntimeError as exc:
                await send_error(exc)
        return True

    async def handle_output(msg) -> None:
        # ---- the whole reason this file exists -------------------------------
        # Tap the message before `schemas.from_msg` throws away `time_s` (and
        # before tool_call messages are dropped on the floor).
        if on_msg is not None:
            try:
                on_msg(msg)
            except Exception:
                # Tracing must never take down a live call.
                logger.exception("on_msg hook failed")
        # ----------------------------------------------------------------------

        schema = schemas.from_msg(msg)
        if schema is None:
            return

        await websocket.send_json(schema.model_dump())
        if msg.msg_type == "audio":
            await websocket.send_bytes(msg.data)

    async def input_loop() -> None:
        while not stop_event.is_set():
            try:
                raw = await websocket.receive()
                if not await handle_input(raw):
                    stop_event.set()
                    await input_handle.close()
                    break
            except (fastapi.WebSocketDisconnect, RuntimeError):
                stop_event.set()
                await input_handle.close()
                break
            except Exception:
                logger.exception("input loop error")
                stop_event.set()
                break

    async def output_loop() -> None:
        while not stop_event.is_set():
            try:
                msg = await output_handle.receive()
                if msg is None:
                    break
                await handle_output(msg)
            except Exception as exc:
                logger.exception("output loop error")
                await send_error(exc)
                break

    await websocket.accept()
    try:
        start_msg = await websocket.receive_json()
        if start_msg.get("type") != "start":
            await websocket.close(code=4000, reason="Expected start message")
            return

        try:
            plan = await on_start(start_msg)
        except RuntimeError as exc:
            logger.error("on_start error: %s", exc)
            await websocket.close(code=4001, reason=str(exc))
            return

        input_handle, output_handle = await gradbot.run(
            **plan.run_kwargs,
            session_config=plan.config,
            input_format=input_format,
            output_format=output_format,
        )

        stop_event = asyncio.Event()
        logger.info("session started")
        results = await asyncio.gather(
            output_loop(),
            input_loop(),
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("task %d raised: %s", i, r)

    except Exception as exc:
        logger.exception("session error")
        await send_error(exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
