"""Deterministic latency collection — the arithmetic half of the analysis.

Runs the runbook's SQL (see `docs/session_latency_analysis.md`, steps 1-6) and
returns a JSON-serializable bundle: per-stage TTFB stats, per-turn perceived
latency (with intro/stall flags + rule-based cause tags), and the LLM-usage
timeline used to tell context-bloat from API jitter. No model call lives here —
an LLM must never do arithmetic on these rows.

This iteration is SQL-only — log cross-check (runbook step 7) is deferred, so the
stall tail is flagged heuristically (threshold, no log confirmation).
"""

from __future__ import annotations

import uuid

import asyncpg

from tracing import LLM_PROCESSOR

# Heuristics (SQL-only; see module docstring).
STALL_S = 30.0       # a "turn" this long is the stall watchdog, never a real turn
STALL_TAIL_S = 6.0   # 6s+ on the LAST turn = stall watchdog firing at hangup
OUTLIER_MULT = 2.0   # a real turn >= this * median perceived latency is an outlier
BLOAT_CORR = 0.5     # LLM ttfb vs prompt-token correlation above this => context bloat


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interp quantile. q in [0, 1]. Returns None for empty input."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation, or None if undefined (too few points / no variance)."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[:n], ys[:n]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def _build_turns(turn_events: list[dict]) -> list[dict]:
    """One entry per real conversational turn, ordered.

    perceived_s = time from the most recent unanswered user_stopped_speaking to
    the bot's FIRST word after it. Only the very first bot word(s), before any
    user has stopped speaking, are the intro (cold start). Later bot-starts with
    no pending user-stop are continuation utterances or duplicate events (the
    events table double-records) and are skipped, not counted. The stall
    watchdog firing shows up as a real turn with a huge gap.
    """
    turns: list[dict] = []
    pending: int | None = None
    seen_user_stop = False
    intro_emitted = False
    n = 0
    for ev in turn_events:
        if ev["kind"] == "user_stopped_speaking":
            pending = ev["timestamp_ns"]
            seen_user_stop = True
        elif ev["kind"] == "bot_started_speaking":
            if pending is not None:
                n += 1
                perceived_s = (ev["timestamp_ns"] - pending) / 1e9
                turns.append({
                    "turn": n,
                    "perceived_s": round(perceived_s, 3),
                    "is_intro": False,
                    "is_stall": False,
                })
                pending = None
            elif not seen_user_stop and not intro_emitted:
                n += 1
                intro_emitted = True
                turns.append({
                    "turn": n, "perceived_s": None, "is_intro": True, "is_stall": False,
                })
            # else: duplicate / continuation bot-start — ignore.

    # Stall flagging: a real turn over STALL_S, or over STALL_TAIL_S on the last
    # turn (near-hangup watchdog). SQL-only, so heuristic (no log confirmation).
    last_real = max(
        (i for i, t in enumerate(turns) if t["perceived_s"] is not None),
        default=None,
    )
    for i, t in enumerate(turns):
        ps = t["perceived_s"]
        if ps is not None and (ps >= STALL_S or (ps >= STALL_TAIL_S and i == last_real)):
            t["is_stall"] = True
    return turns


async def collect_latency_bundle(pool: asyncpg.Pool, sid: uuid.UUID) -> dict:
    """Run the runbook's SQL (steps 1-6) and return the deterministic bundle."""
    async with pool.acquire() as conn:
        s = await conn.fetchrow(
            """
            SELECT persona_name, environment,
                   extract(epoch FROM (ended_at - started_at)) AS duration_s,
                   total_prompt_tokens, total_completion_tokens,
                   total_cache_read_tokens, total_cache_creation_tokens,
                   total_tts_chars
            FROM sessions WHERE id = $1
            """,
            sid,
        )
        if s is None:
            raise KeyError("session_not_found")
        stage = await conn.fetch(
            """
            SELECT processor, count(*) AS n,
                   min(value_num) AS min_v, avg(value_num) AS avg_v, max(value_num) AS max_v
            FROM metrics WHERE session_id = $1 AND kind = 'ttfb'
            GROUP BY processor ORDER BY avg(value_num) DESC
            """,
            sid,
        )
        ttfb_rows = await conn.fetch(
            """
            SELECT ts, processor, value_num FROM metrics
            WHERE session_id = $1 AND kind = 'ttfb' ORDER BY ts
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
        usage_rows = await conn.fetch(
            """
            SELECT ts, processor, prompt_tokens, completion_tokens FROM metrics
            WHERE session_id = $1 AND kind = 'llm_usage' ORDER BY ts
            """,
            sid,
        )

    session = {
        "persona_name": s["persona_name"],
        "environment": s["environment"],
        "duration_s": float(s["duration_s"]) if s["duration_s"] is not None else None,
        "prompt_tokens": s["total_prompt_tokens"],
        "completion_tokens": s["total_completion_tokens"],
        "cache_read_tokens": s["total_cache_read_tokens"],
        "cache_creation_tokens": s["total_cache_creation_tokens"],
        "tts_chars": s["total_tts_chars"],
    }
    stage_ttfb = [
        {
            "processor": r["processor"],
            "n": r["n"],
            "min": round(float(r["min_v"]), 3) if r["min_v"] is not None else None,
            "avg": round(float(r["avg_v"]), 3) if r["avg_v"] is not None else None,
            "max": round(float(r["max_v"]), 3) if r["max_v"] is not None else None,
        }
        for r in stage
    ]
    ttfb_timeline = [
        {"t": r["ts"].strftime("%H:%M:%S"), "processor": r["processor"],
         "ttfb": round(float(r["value_num"]), 3) if r["value_num"] is not None else None}
        for r in ttfb_rows
    ]
    llm_usage_timeline = [
        {"t": r["ts"].strftime("%H:%M:%S"),
         "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"]}
        for r in usage_rows
    ]

    turns = _build_turns([dict(r) for r in turn_events])

    # Perceived-latency stats over real turns only (exclude intro + stall).
    real = [t["perceived_s"] for t in turns
            if not t["is_intro"] and not t["is_stall"] and t["perceived_s"] is not None]
    sorted_real = sorted(real)
    median = _quantile(sorted_real, 0.5)
    perceived_stats = {
        "count": len(real),
        "min": round(min(real), 3) if real else None,
        "avg": round(sum(real) / len(real), 3) if real else None,
        "median": round(median, 3) if median is not None else None,
        "p90": round(_quantile(sorted_real, 0.9), 3) if real else None,
        "max": round(max(real), 3) if real else None,
    }

    # Context-bloat vs API-jitter: do LLM TTFB spikes track prompt-token growth?
    #
    # Sceance infers the LLM stage from whichever processor emits llm_usage rows.
    # Gradbot emits none — its Rust core owns the LLM call and never reports token
    # counts — so that inference would yield None here and silently empty the TTFB
    # series along with it. Name the stage outright instead (tracing.py writes it).
    llm_proc = LLM_PROCESSOR
    llm_ttfb = [float(r["value_num"]) for r in ttfb_rows
                if r["processor"] == llm_proc and r["value_num"] is not None]
    # Always empty in this app: no token counts, so the bloat-vs-jitter
    # correlation cannot be computed and every spike falls through to 'llm_jitter'.
    prompt_series = [r["prompt_tokens"] for r in usage_rows if r["prompt_tokens"] is not None]
    corr = _pearson(llm_ttfb, prompt_series)
    spike_cause = (
        "context_bloat" if corr is not None and corr >= BLOAT_CORR else "llm_jitter"
    )

    # Rule-based cause tags per turn.
    for t in turns:
        if t["is_intro"]:
            t["cause"] = "intro_cold_start"
        elif t["is_stall"]:
            t["cause"] = "stall_watchdog"
        elif median and t["perceived_s"] is not None and t["perceived_s"] >= OUTLIER_MULT * median:
            t["cause"] = spike_cause
        else:
            t["cause"] = None

    return {
        "session": session,
        "stage_ttfb": stage_ttfb,
        "ttfb_timeline": ttfb_timeline,
        "llm_usage_timeline": llm_usage_timeline,
        "turns": turns,
        "perceived_stats": perceived_stats,
        "correlation": {
            "llm_ttfb_vs_prompt_tokens": round(corr, 3) if corr is not None else None,
            "verdict": spike_cause,
            "n": min(len(llm_ttfb), len(prompt_series)),
        },
        "thresholds": {
            "stall_s": STALL_S, "stall_tail_s": STALL_TAIL_S,
            "outlier_mult": OUTLIER_MULT, "bloat_corr": BLOAT_CORR,
        },
    }
