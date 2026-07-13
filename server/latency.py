"""Per-session latency analysis — orchestration facade.

The work is split across three modules, mirroring `docs/session_latency_analysis.md`:

- `latency_collector.collect_latency_bundle` — deterministic SQL + arithmetic.
- `latency_report.synthesize_latency_report` — one Claude call over the bundle.
- `latency_repository` — the upsert / load of the single per-session row.

`generate_and_store` chains collect -> synthesize -> upsert so the admin route
has one import. The pieces are re-exported here for convenience.
"""

from __future__ import annotations

import uuid

import asyncpg
from loguru import logger

from latency_collector import collect_latency_bundle
from latency_report import SYNTH_MODEL, synthesize_latency_report
from latency_repository import load_latency_analysis, upsert_latency_analysis

__all__ = [
    "SYNTH_MODEL",
    "collect_latency_bundle",
    "synthesize_latency_report",
    "load_latency_analysis",
    "upsert_latency_analysis",
    "generate_and_store",
]


async def generate_and_store(pool: asyncpg.Pool, sid: uuid.UUID) -> dict:
    """Collect -> synthesize -> upsert the single per-session row. Returns the analysis."""
    bundle = await collect_latency_bundle(pool, sid)
    report = await synthesize_latency_report(bundle)
    has_unexplained = bool(report.get("unexplained"))
    result = await upsert_latency_analysis(pool, sid, bundle, report, has_unexplained, SYNTH_MODEL)
    logger.info(
        f"latency analysis stored for session {sid} "
        f"(unexplained={has_unexplained}, turns={len(bundle['turns'])})"
    )
    return result
