# Changelog

## 2026-07-14 — Deploy config

- **`.do/app.yaml` declares every variable the app reads.** The four Supabase
  values were a comment; they are now `type: SECRET` entries (values still set in
  the App Platform UI, never committed). A missing secret does *not* fail the
  deploy — `/health` answers without touching Supabase, so the container looks
  alive while sign-in and tracing fail on the first call. The spec now says so.
- **`HOST`, `PORT` and `SUPABASE_DB_PASSWORD` dropped from `.env`.** Nothing read
  them: the bind address is fixed in the Dockerfile (`--host 0.0.0.0 --port
  8282`), which must agree with `http_port` in `.do/app.yaml`.
- **`.gitignore` now covers every `.env` variant** (`server/.env.*`), keeping the
  tracked template via a negation. It previously matched only `server/.env`, so a
  stray `.env.bak` would have been committable with live keys in it.

## 2026-07-13 — Standalone

The repo no longer assumes any sibling project.

- **`CLAUDE.md` and `readme.md` rewritten** to stand alone, and every mention of
  the other repo removed from the source comments too — 15 files. The readme now
  walks through creating a Supabase project, where each of the four values comes
  from, and how to run the schema.
- **`server/.env.template`** — tracked (unlike `.env`), documents every variable.
- **One schema file**, `server/migrations/001_schema.sql`, replacing the seven
  copied migrations. Idempotent, so re-running it is a no-op. It drops the two
  tables this app never touched (`user_profile`, `session_latency_analysis`) and
  seeds an invite code (`gradbot`) so you can create the first account.
  `sessions.framework` now defaults to `'gradbot'`.
- **Both personas run on OpenRouter `meta-llama/llama-4-maverick`.** Léo moves off
  `openai/gpt-4.1`; the `openai` provider is still supported, just unused.

## 2026-07-13 — Align endpointing with the Pipecat build

`flush_duration_s` 0.5 -> **0.2**, matching Silero's `stop_secs` default, which is
what the Pipecat build uses.

This was not a tuning choice, it was a measurement bug. `user_stopped_speaking`
starts the response-latency stopwatch, and the two frameworks were firing it 0.3s
apart — so gradbot appeared 0.5s faster at the median when the real gap was 0.2s,
which is roughly the threshold of perception. The apparent win was mostly the
offset. Both are now 0.2s, so the number means the same thing on both sides (and
gradbot really is snappier at turn end, rather than just appearing so).

## 2026-07-13 — first real calls

Three bugs found by actually phoning the agents:

- **The agent was inaudible.** `app.js` set `ws.binaryType = "arraybuffer"`.
  `SyncedAudioPlayer` dispatches on `data instanceof Blob` to recognise audio, so
  every audio frame fell through to the JSON branch, had no `.type`, and was
  dropped in silence. Removed — the default (`blob`) is what it wants.
- **Transcripts never reached the log.** They went to Postgres and nowhere else,
  so "did it even hear me?" needed a SQL query. `tracing.py` now logs each turn as
  `[user] …` / `[agent] …`.
- **Assistant turns were split in two.** `tts_text` captions lag the audio they
  describe — the final word lands ~0.7s *after* `end_tts_audio` — so closing the
  transcript on that event cut sentences in half ("…what did you" / "say?") and
  truncated the greeting. The transcript now closes on `end_of_turn`.

## 2026-07-13

Bootstrapped the repo — a gradbot twin of sceance, sharing its database, users
and dashboard so the two frameworks can be compared head-to-head.

- **Voice stack**: gradbot (Rust multiplexer) with Gradium STT + TTS and any
  OpenAI-compatible LLM. Replaces Pipecat + Daily + Deepgram + Cartesia.
- **`server/gradbot_session.py`**: a fork of gradbot's
  `websocket.handle_session`, adding an `on_msg` hook. Upstream exposes no way to
  observe a session, and its wire conversion discards the per-stage timings.
  `gradbot` is pinned to `0.1.10` as a result.
- **`server/tracing.py`**: maps gradbot's `MsgOut` stream onto sceance's
  `events` / `metrics` / `messages` schema, so the operator dashboard carried
  over with no changes. Timestamps come from the local monotonic clock at message
  arrival: gradbot's `MsgOut.time_s` is always `0.0`, and its `start_s`/`stop_s`
  are an audio-timeline clock, not a session clock. Trusting them made every
  recorded latency `0.000s` while leaving transcripts looking correct.
- **Migration `007_framework.sql`**: adds `sessions.framework`
  (`'gradbot'` | `'pipecat'`, defaulting to `'pipecat'`) to the shared database.
  Sceance needed no code change. The dashboard gained a framework filter and a
  per-row tag.
- **Personas**: ported `yarden_mini` (Sophie, en) and `inigo_v5_fr` (Léo, fr).
  Inigo moved from Anthropic Haiku to OpenAI `gpt-4.1` — gradbot only speaks the
  OpenAI wire protocol.
- **Frontend**: copied from sceance; only `startSession()` in `app.js` changed,
  swapping Daily for gradbot's `SyncedAudioPlayer` over a raw WebSocket.
- **Not ported**: cross-session memory (both personas had it off), tools (none
  exist), token accounting (gradbot never surfaces LLM usage), and the static
  greeting (gradbot has no speak-this-text API — the agent now opens with a real
  LLM turn).
- **Docker-only local runs**: gradbot ships no macOS x86_64 wheel.

## 2026-07-13 — Drop the dashboard

This app records calls and no longer reads them back. Sessions are monitored from
sceance, which reads the same database and filters on `sessions.framework`.

Removed: the admin API (`/api/sessions`, `/api/aggregate`, the per-session detail
and its LLM-written latency post-mortem), the `/dashboard` and `/sessions/{id}`
pages, and everything only they used — `dashboard_repository`, the four `latency*`
modules, `llm_json`, `persona_snapshot`, `require_admin`, and the `anthropic`
dependency (it was there solely to write the latency report).

The recording path is untouched: `tracing.py` still writes the same `messages`,
`events` and `metrics` rows, tagged `framework = 'gradbot'`. That mapping is now a
cross-repo API with **no local consumer** — nothing here fails if a `kind` string
is renamed; a panel in sceance just goes blank. `tests/test_tracing.py` is the only
thing standing between the two.
