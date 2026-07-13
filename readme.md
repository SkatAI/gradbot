# Gradbot voice

A voice agent on [gradbot](https://github.com/gradium-ai/gradbot) — built as a
twin of [`../sceance`](../sceance), which is the same product on
[Pipecat](https://pipecat.ai). Same personas, same users, same Supabase database,
same operator dashboard. The only thing that differs is the framework underneath.

That's the point: with everything else held constant, the difference in per-turn
latency between the two apps is the difference between the two frameworks.

## Quick start

Everything runs in Docker — gradbot ships no macOS x86_64 wheel, so it does not
import on an Intel Mac.

```
make migrate          # once, against the shared Supabase DB
make build
make run              # http://localhost:8080
make test
```

`server/.env` holds the keys (Supabase, Gradium, OpenAI, OpenRouter). It is
gitignored.

## How a call works

```
browser                          server                        gradium / llm
   │                                │
   │  POST /start-session ─────────►│  auth, persona, capacity
   │  ◄──────── {session_id, ws_url}│  reserves a slot
   │                                │
   │  WS /ws/chat ─────────────────►│  re-verifies the JWT,
   │    {type:'start', session_id}  │  opens the Supabase session row
   │                                │
   │  ══ opus audio ═══════════════►│ ══► STT ─► LLM ─► TTS ══►
   │  ◄══════════════ opus audio ══ │ ◄══════════════════════════
   │                                │
   │                                └─► tracing.py ─► supabase
```

The browser half is gradbot's own `SyncedAudioPlayer` (microphone capture, Opus
encoding, jitter-buffered playback), served straight out of the Python package.

## Two personas

| | language | voice | LLM |
|---|---|---|---|
| **Sophie** (`yarden_mini`) | English | Capucine | Llama 4 Maverick via OpenRouter |
| **Léo** (`inigo_v5_fr`) | French | Leo | GPT-4.1 |

Between them they cover both languages and both OpenAI-compatible LLM providers.

## Known differences from sceance

Not bugs — gradbot limits:

- **No token counts.** Gradbot's Rust core makes the LLM call and never reports
  usage, so the dashboard's token cards read zero for these sessions.
- **No instant greeting.** Sceance speaks a canned opening line straight to TTS,
  dodging the cold-start LLM call. Gradbot has no equivalent, so the agent opens
  with a real generated turn — and that latency is one of the things worth
  measuring.
- **No cross-session memory.** Both ported personas had it switched off anyway.

See `CLAUDE.md` for the full engineering notes, including why
`server/gradbot_session.py` is a fork of upstream and why `gradbot` is pinned.
