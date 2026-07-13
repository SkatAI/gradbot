"""Admin dashboard routes: aggregate stats, session list/detail, latency analysis.

SQL + row→JSON shaping live in `dashboard_repository`, persona snapshot
projection in `persona_snapshot`; these handlers validate params and assemble the
detail page's derived bits (persona view + per-turn response latency).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import CurrentUser, require_admin
from dashboard_repository import aggregate, fetch_session_detail, list_sessions
from deps import get_pool
from latency import generate_and_store
from latency_collector import _quantile
from latency_repository import load_latency_analysis
from persona_snapshot import project_persona_snapshot

router = APIRouter()


def _pair_response_latencies(events: list[dict]) -> list[float]:
    """Pair each user_stopped_speaking with the next bot_started_speaking.

    Events are assumed time-ordered. If the user speaks twice without the
    bot answering, the older pending stop is overwritten (we always pair
    against the most recent unanswered user stop).
    """
    out: list[float] = []
    pending: int | None = None
    for ev in events:
        kind = ev["kind"]
        ts = ev["timestamp_ns"]
        if kind == "user_stopped_speaking":
            pending = ts
        elif kind == "bot_started_speaking" and pending is not None:
            out.append((ts - pending) / 1e9)
            pending = None
    return out


@router.get("/api/aggregate")
async def api_aggregate(
    days: int = Query(7, ge=1, le=365),
    pool=Depends(get_pool),
    _admin: CurrentUser = Depends(require_admin),
):
    return await aggregate(pool, days)


@router.get("/api/sessions")
async def api_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    persona: str | None = Query(None),
    environment: str | None = Query(None),
    # 'gradbot' | 'pipecat' — this app shares its database with sceance.
    framework: str | None = Query(None),
    pool=Depends(get_pool),
    _admin: CurrentUser = Depends(require_admin),
):
    return await list_sessions(pool, limit, offset, persona, environment, framework)


def _response_latency(turn_events: list[dict]) -> dict:
    """Per-turn user→bot response latency over the detail page's events."""
    latencies = _pair_response_latencies(turn_events)
    sorted_l = sorted(latencies)
    return {
        "count": len(latencies),
        "mean": (sum(latencies) / len(latencies)) if latencies else None,
        "median": _quantile(sorted_l, 0.5),
        "p90": _quantile(sorted_l, 0.9),
        "max": max(latencies) if latencies else None,
        "per_turn": [
            {"turn": i + 1, "latency_s": v} for i, v in enumerate(latencies)
        ],
    }


@router.get("/api/sessions/{session_id}")
async def api_session_detail(
    session_id: str,
    pool=Depends(get_pool),
    _admin: CurrentUser = Depends(require_admin),
):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad_uuid")
    detail = await fetch_session_detail(pool, sid)
    if detail is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    turn_events = detail.pop("turn_events")
    persona_json = detail.pop("persona_json")
    detail["persona"] = project_persona_snapshot(persona_json)
    detail["response_latency"] = _response_latency(turn_events)
    # Stored latency analysis (if generated before), loaded with the page.
    detail["latency_analysis"] = await load_latency_analysis(pool, sid)
    return detail


@router.post("/api/sessions/{session_id}/latency-analysis")
async def api_session_latency_analysis(
    session_id: str,
    pool=Depends(get_pool),
    _admin: CurrentUser = Depends(require_admin),
):
    """Run the deterministic collector + one LLM synthesis call, store, return it.

    Single row per session — regenerating overwrites. Synchronous (one LLM call).
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad_uuid")
    try:
        return await generate_and_store(pool, sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="session_not_found")
