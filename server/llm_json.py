"""Shared Anthropic-call + JSON-extraction helpers.

Both memory summarization (`memory.py`) and latency analysis (`latency.py`)
follow the same shape: build an Anthropic client, run one blocking
`messages.create` off the event loop, concatenate the text blocks, then pull a
JSON object out of a response that may be fenced or wrapped in prose. That shape
lives here so the two callers share one implementation.
"""

from __future__ import annotations

import asyncio
import json

import anthropic

DEFAULT_MAX_TOKENS = 1500


async def call_anthropic_text(
    api_key: str | None,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """One blocking Anthropic message call, run off the event loop.

    Returns the concatenated text of all content blocks.
    """
    client = anthropic.Anthropic(api_key=api_key)

    def _run() -> str:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate any text blocks.
        return "".join(getattr(b, "text", "") for b in msg.content)

    return await asyncio.to_thread(_run)


def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Claude is usually compliant but occasionally wraps JSON in prose or fences;
    we strip both, then fall back to greedy brace extraction.
    """
    s = text.strip()
    if s.startswith("```"):
        # Remove leading fence (```json or ```), trailing fence.
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Fall back to greedy brace extraction.
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(s[start : end + 1])
