"""Thin asyncpg wrapper for Supabase Postgres.

One process-wide connection pool, created in the FastAPI lifespan and passed
into each bot session. Also the session repository: named insert/finalize
methods that own the table names and fixed column orders the recorder writes,
so `recorder.py` never deals in raw table strings.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import asyncpg
from loguru import logger

from settings import get_settings


async def create_pool() -> asyncpg.Pool:
    settings = get_settings()
    url = settings.supabase_db_url
    if not url:
        raise RuntimeError("SUPABASE_DB_URL is not set")
    # statement_cache_size=0 is required when connecting through Supabase's
    # Supavisor / pgbouncer pooler (transaction mode). Without it, asyncpg's
    # per-connection prepared-statement names collide across recycled sessions:
    #   DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_1__"
    #   already exists
    pool = await asyncpg.create_pool(
        dsn=url,
        min_size=1,
        max_size=settings.supabase_pool_max,
        command_timeout=10,
        statement_cache_size=0,
    )
    logger.info("Supabase pool created")
    return pool


async def close_pool(pool: asyncpg.Pool | None) -> None:
    if pool is None:
        return
    await pool.close()
    logger.info("Supabase pool closed")


async def bulk_insert(
    pool: asyncpg.Pool,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> None:
    rows = list(rows)
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.copy_records_to_table(table, records=rows, columns=list(columns))


# ---- Session repository ----------------------------------------------------
#
# Fixed column orders for the recorder's child tables — the single source of
# truth, owned here rather than derived from dict keys in the recorder.

MESSAGE_COLUMNS = ("session_id", "role", "text", "language", "stt_timestamp", "recorded_at")
EVENT_COLUMNS = ("session_id", "kind", "timestamp_ns", "payload")
METRIC_COLUMNS = (
    "session_id",
    "processor",
    "model",
    "kind",
    "ts",
    "value_num",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
)


async def _insert_rows(
    pool: asyncpg.Pool, table: str, columns: Sequence[str], rows: Iterable[dict]
) -> None:
    """Project dict rows onto a fixed column order and bulk-insert them."""
    rows = list(rows)
    if not rows:
        return
    await bulk_insert(pool, table, list(columns), [[r[c] for c in columns] for r in rows])


async def insert_messages(pool: asyncpg.Pool, rows: Iterable[dict]) -> None:
    await _insert_rows(pool, "messages", MESSAGE_COLUMNS, rows)


async def insert_events(pool: asyncpg.Pool, rows: Iterable[dict]) -> None:
    await _insert_rows(pool, "events", EVENT_COLUMNS, rows)


async def insert_metrics(pool: asyncpg.Pool, rows: Iterable[dict]) -> None:
    await _insert_rows(pool, "metrics", METRIC_COLUMNS, rows)


# Tags every session row with the stack that recorded it, so more than one voice
# framework can share a single analytics database and still be told apart.
FRAMEWORK = "gradbot"


async def insert_session(
    pool: asyncpg.Pool,
    *,
    session_id,
    persona_name: str,
    persona_json: str,
    lang: str | None,
    started_at,
    user_id,
    environment: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id, persona_name, persona_json, lang, started_at,
                                  user_id, environment, framework)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
            """,
            session_id,
            persona_name,
            persona_json,
            lang,
            started_at,
            user_id,
            environment,
            FRAMEWORK,
        )


async def finalize_session(
    pool: asyncpg.Pool,
    *,
    session_id,
    ended_at,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    tts_chars: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
               SET ended_at                    = $1,
                   total_prompt_tokens         = $2,
                   total_completion_tokens     = $3,
                   total_cache_read_tokens     = $4,
                   total_cache_creation_tokens = $5,
                   total_tts_chars             = $6
             WHERE id = $7
            """,
            ended_at,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            tts_chars,
            session_id,
        )
