"""Read-side queries for the operator dashboard.

Owns the SQL and row→JSON shaping for the aggregate stats, the session list
(with its optional persona/environment filters), and the session-detail core.
Route handlers validate params and call these; they don't write SQL.

Session detail deliberately returns the raw `persona_json` and `turn_events`
alongside the shaped storage fields: persona projection (Task 14) and the
per-turn response-latency math are presentation concerns the route assembles, so
this layer stays storage-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def aggregate(pool: asyncpg.Pool, days: int) -> dict:
    """Rollup stats + per-stage TTFB over the trailing `days` window."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              count(*)                                                       AS total_sessions,
              count(*) FILTER (WHERE ended_at IS NOT NULL)                   AS finished_sessions,
              coalesce(sum(extract(epoch FROM (ended_at - started_at))), 0)  AS total_duration_s,
              coalesce(sum(total_prompt_tokens), 0)                          AS prompt_tokens,
              coalesce(sum(total_completion_tokens), 0)                      AS completion_tokens,
              coalesce(sum(total_cache_read_tokens), 0)                      AS cache_read_tokens,
              coalesce(sum(total_cache_creation_tokens), 0)                  AS cache_creation_tokens,
              coalesce(sum(total_tts_chars), 0)                              AS tts_chars
            FROM sessions
            WHERE started_at >= $1
            """,
            since,
        )
        ttfb = await conn.fetch(
            """
            SELECT processor, avg(value_num) AS avg_ttfb, count(*) AS n
            FROM metrics m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.kind = 'ttfb' AND s.started_at >= $1
            GROUP BY processor
            ORDER BY processor
            """,
            since,
        )
    return {
        "since": _iso(since),
        "days": days,
        "sessions": dict(row),
        "ttfb": [
            {"processor": r["processor"], "avg_ttfb": float(r["avg_ttfb"]), "n": r["n"]}
            for r in ttfb
        ],
    }


def _session_filter(
    persona: str | None, environment: str | None, framework: str | None
) -> tuple[str, list]:
    """Build the shared WHERE clause + positional args for the session list.

    Returns ('', []) when unfiltered. Only fixed column names are interpolated;
    user values go through asyncpg placeholders ($1, $2), never string formatting.
    """
    conditions: list[str] = []
    args: list = []
    if persona:
        args.append(persona)
        conditions.append(f"s.persona_name = ${len(args)}")
    if environment:
        args.append(environment)
        conditions.append(f"s.environment = ${len(args)}")
    if framework:
        args.append(framework)
        conditions.append(f"s.framework = ${len(args)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, args


async def list_sessions(
    pool: asyncpg.Pool,
    limit: int,
    offset: int,
    persona: str | None,
    environment: str | None,
    framework: str | None = None,
) -> dict:
    """Paginated, optionally-filtered session list + filter dropdown options.

    `framework` ('gradbot' | 'pipecat') exists because this app and sceance share
    one database. Without it the ledger silently interleaves calls from both
    stacks, which is confusing at best and misleading when comparing latency.
    """
    where, filter_args = _session_filter(persona, environment, framework)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT s.id, s.persona_name, s.environment, s.framework, s.lang,
                   s.started_at, s.ended_at,
                   extract(epoch FROM (s.ended_at - s.started_at)) AS duration_s,
                   s.total_prompt_tokens, s.total_completion_tokens,
                   s.total_cache_read_tokens, s.total_cache_creation_tokens,
                   s.total_tts_chars,
                   (SELECT count(*) FROM messages WHERE session_id = s.id) AS msg_count
            FROM sessions s
            {where}
            ORDER BY s.started_at DESC
            LIMIT ${len(filter_args) + 1} OFFSET ${len(filter_args) + 2}
            """,
            *filter_args, limit, offset,
        )
        total = await conn.fetchval(
            f"SELECT count(*) FROM sessions s {where}", *filter_args
        )
        # Distinct persona names across ALL sessions, for the filter dropdown.
        persona_rows = await conn.fetch(
            """
            SELECT DISTINCT persona_name FROM sessions
            WHERE persona_name IS NOT NULL
            ORDER BY persona_name
            """
        )
        # Distinct environments across ALL sessions, for the filter dropdown.
        env_rows = await conn.fetch(
            """
            SELECT DISTINCT environment FROM sessions
            WHERE environment IS NOT NULL
            ORDER BY environment
            """
        )
        framework_rows = await conn.fetch(
            """
            SELECT DISTINCT framework FROM sessions
            WHERE framework IS NOT NULL
            ORDER BY framework
            """
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "personas": [r["persona_name"] for r in persona_rows],
        "environments": [r["environment"] for r in env_rows],
        "frameworks": [r["framework"] for r in framework_rows],
        "sessions": [
            {
                "id": str(r["id"]),
                "persona_name": r["persona_name"],
                "environment": r["environment"],
                "framework": r["framework"],
                "lang": r["lang"],
                "started_at": _iso(r["started_at"]),
                "ended_at": _iso(r["ended_at"]),
                "duration_s": float(r["duration_s"]) if r["duration_s"] is not None else None,
                "prompt_tokens": r["total_prompt_tokens"],
                "completion_tokens": r["total_completion_tokens"],
                "cache_read_tokens": r["total_cache_read_tokens"],
                "cache_creation_tokens": r["total_cache_creation_tokens"],
                "tts_chars": r["total_tts_chars"],
                "msg_count": r["msg_count"],
            }
            for r in rows
        ],
    }


async def fetch_session_detail(pool: asyncpg.Pool, sid: uuid.UUID) -> dict | None:
    """Shaped session-detail core, or None if the session does not exist.

    `persona_json` (raw) and `turn_events` are returned for the route to project
    the persona and compute response latency; everything else is final-shaped.
    """
    async with pool.acquire() as conn:
        s = await conn.fetchrow(
            """
            SELECT id, persona_name, persona_json, lang, started_at, ended_at,
                   extract(epoch FROM (ended_at - started_at)) AS duration_s,
                   total_prompt_tokens, total_completion_tokens,
                   total_cache_read_tokens, total_cache_creation_tokens,
                   total_tts_chars
            FROM sessions WHERE id = $1
            """,
            sid,
        )
        if s is None:
            return None
        messages = await conn.fetch(
            "SELECT role, text, language, recorded_at FROM messages "
            "WHERE session_id = $1 ORDER BY id",
            sid,
        )
        metrics = await conn.fetch(
            """
            SELECT processor, kind,
                   count(*)              AS n,
                   avg(value_num)        AS avg_value,
                   sum(value_num)        AS sum_value
            FROM metrics
            WHERE session_id = $1
            GROUP BY processor, kind
            ORDER BY processor, kind
            """,
            sid,
        )
        turn_events = await conn.fetch(
            """
            SELECT kind, timestamp_ns FROM events
            WHERE session_id = $1
              AND kind IN ('user_stopped_speaking', 'bot_started_speaking')
            ORDER BY timestamp_ns
            """,
            sid,
        )
    return {
        "id": str(s["id"]),
        "persona_name": s["persona_name"],
        "persona_json": s["persona_json"],
        "lang": s["lang"],
        "started_at": _iso(s["started_at"]),
        "ended_at": _iso(s["ended_at"]),
        "duration_s": float(s["duration_s"]) if s["duration_s"] is not None else None,
        "prompt_tokens": s["total_prompt_tokens"],
        "completion_tokens": s["total_completion_tokens"],
        "cache_read_tokens": s["total_cache_read_tokens"],
        "cache_creation_tokens": s["total_cache_creation_tokens"],
        "tts_chars": s["total_tts_chars"],
        "messages": [
            {
                "role": m["role"],
                "text": m["text"],
                "language": m["language"],
                "recorded_at": _iso(m["recorded_at"]),
            }
            for m in messages
        ],
        "metrics": [
            {
                "processor": m["processor"],
                "kind": m["kind"],
                "n": m["n"],
                "avg": float(m["avg_value"]) if m["avg_value"] is not None else None,
                "sum": float(m["sum_value"]) if m["sum_value"] is not None else None,
            }
            for m in metrics
        ],
        "turn_events": [{"kind": e["kind"], "timestamp_ns": e["timestamp_ns"]} for e in turn_events],
    }
