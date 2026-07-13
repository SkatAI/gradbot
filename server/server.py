"""FastAPI app for the gradbot voice agent.

Wires the app together: lifespan (asyncpg pool), CORS, the route modules under
`routes/`, and two static mounts. Route handlers live in
`routes/{public,auth,sessions,admin}.py`.

Run locally — in Docker, always:
    make build && make run
(gradbot ships no macOS x86_64 wheel, so it does not import on an Intel Mac.)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import gradbot
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

import session_tasks
from routes import auth, public, sessions
from settings import get_settings
from storage import close_pool, create_pool

# gradbot ships the browser half of its audio stack — the Opus encoder, the mic
# worklet, and SyncedAudioPlayer — inside the Python package. Serving it from
# there is what replaces Daily on the client: mic capture, encoding and
# jitter-buffered playback all come from these files.
JS_AUDIO_DIR = Path(gradbot.__file__).parent / "js_audio"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Without this the Rust core's logs never surface — and it is the half of the
    # stack we can't step through.
    gradbot.init_logging()
    if not settings.gradium_api_key:
        logger.warning("GRADIUM_API_KEY is not set — every call will fail (STT and TTS)")
    logger.info(f"Static dir: {settings.static_dir}, MAX_SESSIONS={settings.max_sessions}")
    app.state.db_pool = await create_pool()
    try:
        yield
    finally:
        session_tasks.reset()
        await close_pool(app.state.db_pool)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8282",
        "http://127.0.0.1:8282",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*", "Authorization"],
)

# Deliberately NOT gradbot.routes.setup(): it mounts "/" itself and would fight
# the app's own root mount below. Mount just the piece we want, and mount it
# before "/" — Starlette matches mounts in registration order.
app.mount("/static/js", StaticFiles(directory=str(JS_AUDIO_DIR)), name="gradbot_js")

app.include_router(public.router)
app.include_router(auth.router)
app.include_router(sessions.router)
# No admin/dashboard router: this app records sessions but never reads them back.
# Traces are read by whatever you point at the database.

# Mount the app's own static last so the API routes above take precedence.
_static_dir = get_settings().static_dir
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
