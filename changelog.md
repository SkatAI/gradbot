# Changelog

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
