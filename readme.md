# Gradbot voice

A voice agent you can phone up in the browser, built on
[gradbot](https://github.com/gradium-ai/gradbot) — Gradium's Rust-core framework
for real-time speech.

Pick a character, click *Let's talk*, and they answer. Speech-to-text, an LLM, and
text-to-speech run as one continuous duplex stream, so the agent can be
interrupted mid-sentence and will stop and listen, the way a person does.

Every call is recorded to Postgres — full transcript, per-turn latency broken down
by stage — and there's an operator dashboard to read it back.

## Quick start

Everything runs in Docker. Gradbot ships no macOS x86_64 wheel, so on an Intel Mac
the container is the only place it imports.

```
make            # list the available commands
make migrate    # once — creates the schema in Supabase
make build
make run        # http://localhost:8282
```

Sign in with your email (passwordless — no email is actually sent), pick an agent,
and allow microphone access.

`server/.env` holds the API keys: Supabase, Gradium, and one key per LLM provider
you use. It is gitignored.

## Who you can call

| | language | voice | LLM |
|---|---|---|---|
| **Sophie** | English | Capucine | Llama 4 Maverick, via OpenRouter |
| **Léo** | French | Leo | GPT-4.1 |

Sophie is a French farmer with strong opinions about the price of eggs. Léo is a
discernment guide in the Ignatian tradition — you talk, he listens.

Personas are plain JSON in `personas/`, one file each. Point one at a different
voice, prompt, or model and restart:

```json
{
  "agent":   { "active": true, "lang": "fr", "visibility": "public" },
  "persona": { "name": "Leo", "system_prompt_path": "…", "greeting": "Bonjour…" },
  "llm":     { "provider": "openai", "model": "gpt-4.1" },
  "tts":     { "provider": "gradium", "voice_id": "axlOaUiFyOZhy4nv" },
  "gradbot": { "silence_timeout_s": 0.0, "assistant_speaks_first": true }
}
```

Any **OpenAI-compatible** LLM endpoint works — OpenAI, OpenRouter, Groq, a local
Ollama. That's the only wire protocol gradbot speaks. Speech is Gradium for both
STT and TTS.

## How a call works

```
browser                          server                        gradium / llm
   │                                │
   │  POST /start-session ─────────►│  auth, persona, capacity
   │  ◄──────── {session_id, ws_url}│  reserves a slot
   │                                │
   │  WS /ws/chat ─────────────────►│  re-verifies the JWT,
   │    {type:'start', session_id}  │  opens the session row
   │                                │
   │  ══ opus audio ═══════════════►│ ══► STT ─► LLM ─► TTS ══►
   │  ◄══════════════ opus audio ══ │ ◄══════════════════════════
   │                                │
   │                                └─► tracing ─► postgres
```

The browser half is gradbot's own `SyncedAudioPlayer` — microphone capture, Opus
encoding, and jitter-buffered playback — served straight out of the Python package.
No frontend build step, no npm.

## The dashboard

`/dashboard` (admin only) lists every call and drills into one: the transcript, a
per-turn latency chart, and time-to-first-byte for each stage of the pipeline
(LLM, then TTS). There's also a one-click LLM-written post-mortem of a call's
latency — what was slow, and whether it looks like model jitter or a bloated
context.

## Known limitations

These come from gradbot itself, and are worth knowing before you build on it:

- **No token accounting.** The Rust core makes the LLM call and never reports
  usage, so the dashboard's token counters read zero.
- **No canned opening line.** There is no speak-this-text API, so the agent's
  first turn is a real LLM generation — you'll hear a cold-start pause of a second
  or two before it says hello.
- **No hook to observe a session.** Gradbot's WebSocket bridge forwards everything
  to the browser and offers nothing to the server, so `server/gradbot_session.py`
  is a fork of it with a tracing hook added. That's why `gradbot` is pinned.

See `CLAUDE.md` for the engineering notes.
