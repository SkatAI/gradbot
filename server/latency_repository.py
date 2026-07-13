"""Persistence for the single per-session latency-analysis row.

Owns the `session_latency_analysis` table: the upsert (one row per session;
regenerating overwrites) and the load-mapping that the admin session-detail page
uses. Keeping the `json.loads`/datetime shaping here means the route never parses
stored latency JSON inline.
"""

from __future__ import annotations

import json
import uuid

import asyncpg


def _coerce_json(value):
    """asyncpg may hand back jsonb columns as str or already-decoded objects."""
    return json.loads(value) if isinstance(value, str) else value


def _shape(bundle, report, has_unexplained, model, generated_at) -> dict:
    return {
        "bundle": bundle,
        "report": report,
        "has_unexplained": has_unexplained,
        "model": model,
        "generated_at": generated_at.isoformat() if generated_at else None,
    }


async def upsert_latency_analysis(
    pool: asyncpg.Pool,
    sid: uuid.UUID,
    bundle: dict,
    report: dict,
    has_unexplained: bool,
    model: str,
) -> dict:
    """Insert or overwrite the per-session analysis row; return the shaped dict."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO session_latency_analysis
                (session_id, bundle, report, has_unexplained, model, generated_at)
            VALUES ($1, $2::jsonb, $3::jsonb, $4, $5, now())
            ON CONFLICT (session_id) DO UPDATE
              SET bundle = EXCLUDED.bundle,
                  report = EXCLUDED.report,
                  has_unexplained = EXCLUDED.has_unexplained,
                  model = EXCLUDED.model,
                  generated_at = now()
            RETURNING generated_at
            """,
            sid, json.dumps(bundle), json.dumps(report), has_unexplained, model,
        )
    return _shape(bundle, report, has_unexplained, model, row["generated_at"])


async def load_latency_analysis(pool: asyncpg.Pool, sid: uuid.UUID) -> dict | None:
    """Load the stored analysis for a session, shaped for the API, or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT bundle, report, has_unexplained, model, generated_at
            FROM session_latency_analysis WHERE session_id = $1
            """,
            sid,
        )
    if row is None:
        return None
    return _shape(
        _coerce_json(row["bundle"]),
        _coerce_json(row["report"]),
        row["has_unexplained"],
        row["model"],
        row["generated_at"],
    )
