"""LLM synthesis — the model half of the latency analysis.

One Claude call (Sonnet 4.6) turns the deterministic bundle from
`latency_collector` into a readable operator report and, crucially, flags
anything that does not fall into the known cause buckets. All arithmetic is
already done in the bundle; the model must not recompute it.
"""

from __future__ import annotations

import json
import os

from llm_json import call_anthropic_text, extract_json_object

SYNTH_MODEL = "claude-sonnet-4-6"

_SYNTH_SYSTEM = """You are a latency analyst for a real-time voice-agent service
(STT -> LLM -> TTS cascade). You receive a pre-computed JSON bundle of one
session's latency — all numbers are already calculated; DO NOT recompute or
second-guess them. Your job is to write a short operator-facing report and, most
importantly, flag anything you cannot explain.

Definitions:
- "Perceived latency" = wall-clock from the user finishing their turn to the
  bot's first spoken word. This is the headline number. Report median + max over
  real turns (intro and stall are already excluded from perceived_stats).
- Per-stage TTFB = time-to-first-token for each stage (STT/LLM/TTS). Diagnostic:
  tells you which stage is slow. The LLM is normally dominant and most variable.

Known cause buckets (each outlier turn in the bundle is pre-tagged with one):
- intro_cold_start: the first bot word, generated through a cold LLM connection.
  Expected ~4s; off the critical path of real turns.
- stall_watchdog: a huge gap (>= stall thresholds), usually the STT stall
  watchdog firing at hangup. NOT a real conversational turn — exclude it.
- llm_jitter: a slow real turn whose LLM TTFB spike does NOT track prompt-token
  growth (correlation low) => OpenAI/provider-side jitter, not your context.
- context_bloat: LLM TTFB tracks prompt-token growth (correlation high) =>
  trimming history would help.

Write STRICT JSON with exactly these keys, no prose outside the object:
- "headline": one sentence — real turns, perceived median and max.
- "main_cause": one or two sentences naming the dominant latency driver, citing
  the per-stage averages and whether spikes are jitter or context bloat.
- "outliers": array of {"turn": int, "perceived_s": number, "cause": string,
  "note": string}. One per intro/stall/outlier turn, each with a plain-English
  reason. Use the pre-assigned cause tag.
- "unexplained": array of strings. List anything that does NOT fit a known
  bucket or that the data cannot account for — a stage slow for no clear reason,
  a tag that contradicts the numbers, missing/zero data where you'd expect some,
  a correlation that's null when it matters. EMPTY array if everything is
  explained. Be honest; this is the signal for a human to look closer."""


async def synthesize_latency_report(bundle: dict, model: str = SYNTH_MODEL) -> dict:
    """One Claude call: bundle -> structured report. Raises on hard failure."""
    prompt = (
        "Here is the latency bundle for one session. Produce the JSON now.\n\n"
        + json.dumps(bundle, indent=2)
    )
    raw = await call_anthropic_text(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model=model,
        system=_SYNTH_SYSTEM,
        prompt=prompt,
    )
    report = extract_json_object(raw)
    # Normalise the keys we depend on downstream.
    report.setdefault("headline", "")
    report.setdefault("main_cause", "")
    report.setdefault("outliers", [])
    report.setdefault("unexplained", [])
    if not isinstance(report["unexplained"], list):
        report["unexplained"] = [str(report["unexplained"])]
    return report
