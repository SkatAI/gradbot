# Gradbot voice

A browser voice agent on [gradbot](https://github.com/gradium-ai/gradbot) —
Gradium's Rust-core framework for real-time speech. FastAPI backend, Supabase for
accounts and call traces, no frontend build step.

`readme.md` is the user-facing setup guide. This file is the engineering notes:
the things that will bite you, and why the code is shaped the way it is.

## Non-negotiables

### Run it in Docker. Always.

`gradbot` publishes macOS wheels for **arm64 only**. This machine is an Intel Mac
with no Rust toolchain to build the sdist from source, so **`import gradbot`
cannot work outside a linux/amd64 container**. `uv sync` on the host will fail.

    make            # list every target
    make build      # after any change under server/*.py
    make run        # detached, on :8282
    make log        # follow it
    make test       # pytest, in the same image

`personas/` and `server/static/` are mounted read-only into the container, so
persona and frontend edits take effect on `make restart` with no rebuild. Python
changes need `make build`.

### `server/gradbot_session.py` is a fork. `gradbot` is pinned.

Gradbot's `websocket.handle_session()` gives you no way to observe a session: it
reads `MsgOut` objects off the Rust multiplexer and forwards them straight to the
browser, and `schemas.from_msg()` *drops `time_s` on the way*. Every transcript
and timing this app records only exists inside that loop.

So `gradbot_session.py` is a copy of it with an `on_msg` hook added. `gradbot` is
pinned to `==0.1.10` in `server/pyproject.toml` **because** this file is a copy.
If you unpin it, diff the fork against upstream's `gradbot_py/gradbot/websocket.py`
at the new tag first. Its three deliberate divergences are listed in its header.

### Don't call `gradbot.routes.setup()`

It mounts `/` itself and would fight the app's own static mount. `server.py`
mounts only the piece we want — gradbot's bundled browser audio at `/static/js` —
and mounts it *before* `/`, because Starlette matches mounts in registration
order.

### `silence_timeout_s` must be `0.0`

Gradbot defaults it to 5s, which makes the agent re-prompt itself with its own
last message whenever the caller goes quiet — it talks to itself. Both personas
pin it to zero and a test enforces it. Upstream's own skill docs say the same.

## Do not trust gradbot's timing fields. We measured them.

`MsgOut` looks like it carries a clock. It does not:

- **`time_s` is always `0.0`** on events. The type stub declares it and the Rust
  core sends it, but the binding never populates it.
- **`start_s` / `stop_s`** (on `audio` and `tts_text`) are an **audio-timeline**
  clock — position *within the synthesized speech* (0.000, 0.080, 0.160, …). Two
  turns an hour apart both begin at 0.0.

The first version of `tracing.py` believed `time_s`. Every event in a turn
collapsed onto one timestamp and every latency recorded as `0.000s` — while the
transcripts stayed perfect, so nothing else looked wrong. **Timestamps now come
from `time.monotonic_ns()` at message arrival in the output loop**, the only
honest clock available. `test_tracing.py` has regression tests that fail if
anyone reaches for those fields again.

Two more measured facts, both counter-intuitive:

- **Captions lag the audio.** A turn's final `tts_text` can arrive ~0.7s *after*
  `end_tts_audio`. Closing the transcript on that event splits sentences across
  two rows ("…what did you" / "say?"). The assistant turn closes on `end_of_turn`.
- **`user_stopped_speaking` fires after STT has already produced text.** Gradbot's
  `Flushing` event comes after the transcript, so STT time is hidden inside the
  flush window. Perceived latency measured from it therefore *excludes STT* — know
  that before quoting the number anywhere.

## This app records but never reads

Calls are written to Postgres as they happen — full transcript, turn-taking
events, per-stage time-to-first-byte. There is **no dashboard here**: no
`/api/sessions`, no admin routes, no read side at all. Traces are read by whatever
you point at the database.

The practical consequence: **`server/tracing.py` is an API with no local
consumer.** Rename an `events.kind` and nothing in this repo fails — every test
still passes — while whatever reads the traces quietly goes blank.
`tests/test_tracing.py` pins the exact strings for that reason. Treat them as a
public interface, not as local names.

The vocabulary it writes: `user_stopped_speaking` → `bot_started_speaking` bracket
the silence a caller actually experiences; `metrics.processor` is `GradbotLLM` or
`GradiumTTS`; `sessions.framework` tags which stack recorded the row, so several
can share one analytics database.

**No `llm_usage` rows.** The Rust core owns the LLM call and never surfaces token
counts, so `sessions.total_*_tokens` are always 0.

## Personas

JSON in `personas/`, one file each, the source of truth. There is no persona CRUD.

| | lang | voice (Gradium) | LLM |
|---|---|---|---|
| `yarden_mini` — Sophie | en | `P4GqVY98hjQSvkiu` Capucine | OpenRouter `meta-llama/llama-4-maverick` |
| `inigo_v5_fr` — Léo | fr | `7HhpTMy55D4HkXen` Vianney | OpenRouter `meta-llama/llama-4-maverick` |

`llm.provider` **must be OpenAI-compatible** — gradbot speaks that wire protocol
and nothing else. A provider resolves to an API key plus a base URL
(`personas.LLM_PROVIDERS`); `openai` means the OpenAI endpoint itself.

**Verify a model id is still live with the provider before putting it in a
persona.** A removed model fails at call time, not at load time.

Two capabilities gradbot simply does not have, which the schema reflects:

- **No canned opening line.** `SessionInputHandle` is only `send_audio` /
  `send_config` / `close` — there is no speak-this-text API. The agent's first
  turn is a real LLM generation, so the greeting is folded into the system prompt
  (`prompting.py`) and turn zero costs a second or two.
- **No context seeding.** No way to inject prior messages, so there is nothing
  like an opening-messages list.

## Database

One schema file, `server/migrations/001_schema.sql`, idempotent, run once:

    make migrate

Sign-in is passwordless and **sends no email**: the server holds the service-role
key, mints a one-time `token_hash` via GoTrue's admin API, and the browser redeems
it with `verifyOtp`. An unknown email needs an invite code or it joins the
waitlist. Codes and admin flags are managed by hand in SQL — there is no UI.

There are **no RLS policies**, deliberately. The server connects as the database
owner over `SUPABASE_DB_URL` and bypasses RLS; the browser never touches Postgres
directly. If you point anything else at this database, add policies first.

## Layout

- `server/gradbot_session.py` — the forked bridge. Read its header before touching it.
- `server/tracing.py` — `MsgOut` → `events` / `metrics` / `messages`.
- `server/agent.py` — persona → `SessionConfig` + `gradbot.run()` kwargs. With
  `gradbot_session.py`, the only two modules that import gradbot — which is what
  keeps everything else testable outside the container.
- `server/routes/sessions.py` — `POST /start-session` reserves a slot;
  `WS /ws/chat` runs the call. Two-phase because the session *is* the WebSocket.
- `server/static/app.js` — the whole frontend. `SyncedAudioPlayer` (gradbot's own
  bundled browser audio) plus a raw WebSocket. No build step, no npm.

## Frontend gotcha

Leave `ws.binaryType` at its default (`"blob"`). `SyncedAudioPlayer` recognises
audio via `data instanceof Blob`; set it to `"arraybuffer"` and every audio frame
falls through to the JSON branch, has no `.type`, and is **dropped in silence** —
the agent speaks and the browser throws the sound away. This cost an afternoon.
