# Gradbot voice

A voice agent built on [gradbot](https://github.com/gradium-ai/gradbot) (Gradium's
Rust-core framework). It is a deliberate **twin of `../sceance`**, which is the
same product on Pipecat — same personas, same users, same database, same
dashboard. The point is a head-to-head comparison: latency, quality, and what it
costs a developer to work in each framework.

Read `../sceance/CLAUDE.md` first if you don't know that app. Almost everything
here is copied from it; this file only records where the two diverge and why.

## Non-negotiables

### Run it in Docker. Always.

`gradbot` publishes macOS wheels for **arm64 only**. This is an Intel Mac, there
is no Rust toolchain to build the sdist, so **`import gradbot` cannot work
outside a linux/amd64 container**. `uv sync` on the host will fail.

    make build && make run     # the app, on :8282
    make test                  # pytest, in the same image

Code changes need a rebuild — nothing hot-reloads through the image.

### `server/gradbot_session.py` is a fork. `gradbot` is pinned.

Gradbot's `websocket.handle_session()` gives you no way to observe a session: it
reads `MsgOut` objects off the Rust multiplexer and forwards them to the browser,
and `schemas.from_msg()` *drops `time_s` on the floor* on the way. Every timing
this app records only exists inside that loop.

So `gradbot_session.py` is a copy of it with an `on_msg` hook added. `gradbot` is
pinned to `==0.1.10` in `server/pyproject.toml` because of that. **If you unpin
it, diff the fork against upstream's `gradbot_py/gradbot/websocket.py` first.**

The fork's other two divergences are documented in its header.

### Don't call `gradbot.routes.setup()`

It mounts `/` itself and would fight the app's own static mount. `server.py`
mounts just the piece we need — gradbot's bundled browser audio (`/static/js`) —
and mounts it *before* `/`, because Starlette matches mounts in registration
order.

### `silence_timeout_s` must be `0.0`

Gradbot defaults it to 5 s, which makes the agent re-prompt itself with its own
last message whenever the user goes quiet — it talks to itself. Both personas pin
it to zero, and a test enforces that.

## Shared database

This app and sceance write to **one Supabase project**. `sessions.framework`
(`'gradbot'` | `'pipecat'`) tells them apart; it defaults to `'pipecat'` so
historical rows backfill correctly and **sceance needed no code change**.

Only one migration belongs to this app:

    make migrate     # psql -f server/migrations/007_framework.sql

`001`–`006` are copied in for reference only — they are already applied.

The dashboard shows both stacks' calls, with a framework filter and a per-row
tag. Remember that when reading the ledger: an unfiltered latency figure is an
average across two different frameworks.

## What this app does NOT have

Each of these is a gradbot limitation, not an oversight:

- **Token counts.** The Rust core owns the LLM call and never surfaces usage. So
  `sessions.total_*_tokens` are always 0 here and the dashboard's token KPI cards
  read zero. `latency_collector.LLM_PROCESSOR` is pinned to a constant for the
  same reason — sceance infers the LLM stage from `llm_usage` rows, and there are
  none.
- **A static greeting.** There is no speak-this-text API (`SessionInputHandle` is
  only `send_audio` / `send_config` / `close`), so the agent opens with a real
  LLM turn and the greeting is folded into the system prompt. Turn-zero latency is
  therefore a real number here, and measuring it is part of the point.
- **Cross-session memory.** Both ported personas are memory-off, so `memory.py`,
  `user_profile` and the summarizer were never ported.
- **Tools.** Gradbot supports them; neither persona uses them.

## Personas

Two, ported from sceance (`personas/*.json`, JSON is the source of truth):

| | lang | voice (Gradium) | LLM |
|---|---|---|---|
| `yarden_mini` — Sophie | en | `P4GqVY98hjQSvkiu` Capucine | OpenRouter `meta-llama/llama-4-maverick` |
| `inigo_v5_fr` — Léo | fr | `axlOaUiFyOZhy4nv` Leo | OpenAI `gpt-4.1` |

The schema is slimmer than sceance's — every Pipecat/Deepgram/Cartesia field is
gone. `llm.provider` **must be OpenAI-compatible**; gradbot speaks nothing else.
Sceance's Inigo ran on Anthropic Haiku, which is why it moved to `gpt-4.1` here.

Verify a model id is still live with the provider before putting it in a persona.

## The tracing contract

`server/tracing.py` translates gradbot's `MsgOut` stream into sceance's schema.
This is what let the dashboard carry over untouched, and it is a **contract with
code in another repo** — sceance derives `response_latency` purely from the gap
between a `user_stopped_speaking` event and the next `bot_started_speaking`, and
groups TTFB by `metrics.processor`.

Rename a `kind` string here and a panel over there goes blank. Silently.
`tests/test_tracing.py` pins the exact strings for that reason.

### Do not trust gradbot's timing fields. We measured them.

`MsgOut` looks like it carries a clock. It does not:

- **`time_s` is always `0.0`** on events. The type stub declares it and the Rust
  core sends it, but the binding never populates it.
- **`start_s` / `stop_s`** (on `audio` and `tts_text`) are an **audio-timeline**
  clock — position *within the synthesized speech* (0.000, 0.080, 0.160, …). Two
  turns an hour apart both begin at 0.0.

The first version of `tracing.py` believed `time_s`. Every event in a turn
collapsed onto one timestamp and every latency recorded as `0.000s` — while the
transcripts stayed perfect, so nothing else looked wrong. **Timestamps now come
from `time.monotonic_ns()` at message arrival in the output loop**, which is the
only honest clock available. `test_tracing.py` has regression tests that fail if
anyone reaches for those fields again.

Note this is a slightly different measurement basis than sceance, which stamps
from Pipecat's frame timestamps. Both measure arrival at the Python layer, so
they are comparable — but say so when quoting the numbers.

## Layout

- `server/gradbot_session.py` — the forked bridge. Read its header before touching.
- `server/tracing.py` — `MsgOut` → `events` / `metrics` / `messages`.
- `server/agent.py` — persona → `SessionConfig` + `gradbot.run()` kwargs. With
  `gradbot_session.py`, the only two modules that import gradbot (so everything
  else stays testable).
- `server/routes/sessions.py` — `POST /start-session` reserves a slot;
  `WS /ws/chat` runs the call. Two-phase because the session *is* the WebSocket.
- `server/static/app.js` — the only frontend file that differs from sceance:
  `startSession()` drives `SyncedAudioPlayer` + a WebSocket instead of Daily.
