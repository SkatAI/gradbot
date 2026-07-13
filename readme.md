# Gradbot voice

A voice agent you can phone up in the browser, built on
[gradbot](https://github.com/gradium-ai/gradbot) — Gradium's Rust-core framework
for real-time speech.

Pick a character, click *Let's talk*, and they answer. Speech-to-text, an LLM and
text-to-speech run as one continuous duplex stream, so you can interrupt the agent
mid-sentence and it stops and listens, the way a person does.

Every call is recorded to Postgres: the full transcript, turn-taking events, and
time-to-first-byte for each stage of the pipeline.

---

## What you need

| | |
|---|---|
| **Docker** | The app only runs in a container. Gradbot ships no macOS x86_64 wheel, so on an Intel Mac there is nowhere else it will even import. |
| **A Supabase project** | Free tier is fine. Postgres + the accounts system. |
| **A Gradium API key** | [gradium.ai](https://gradium.ai) — one key covers both speech-to-text and text-to-speech. |
| **An OpenRouter API key** | [openrouter.ai/keys](https://openrouter.ai/keys) — both shipped personas run on OpenRouter. |
| **`psql`** | To create the schema, once. `brew install libpq` on a Mac. |

---

## 1. Set up Supabase

**Create the project.** Sign up at [supabase.com](https://supabase.com), click
*New project*, give it a name and a database password — **write that password
down**, it appears in the connection string later and cannot be recovered, only
reset.

Give it a minute or two to provision, then collect four values.

**`SUPABASE_URL` and `SUPABASE_KEY`** — *Project Settings → Data API*. The URL
looks like `https://abcdefgh.supabase.co`. The key you want is the **anon /
publishable** one; it is safe in the browser, and the app serves it from
`/api/config` so the sign-in widget can use it.

**`SUPABASE_SERVICE_ROLE_KEY`** — *Project Settings → API Keys*, the **secret /
`service_role`** key. This one bypasses every access rule. **Server only. Never
send it to a browser.** The app needs it to create a sign-in token without
emailing anybody (see [Accounts](#accounts) below).

**`SUPABASE_DB_URL`** — *Project Settings → Database → Connection string → URI*.
Replace `[YOUR-PASSWORD]` with the database password from earlier. **Take the
pooled connection** (host contains `pooler.supabase.com`, port `6543`), not the
direct one — the app is already configured for the pooler.

```
postgresql://postgres.abcdefgh:YOUR-PASSWORD@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
```

You do **not** need to enable anything else. Auth is on by default, and the app
uses no storage, no edge functions, and no realtime.

## 2. Fill in the environment

```bash
cp server/.env.template server/.env
$EDITOR server/.env
```

`server/.env` is gitignored; `server/.env.template` is not, so keep secrets out of
the template. Every variable is documented in it.

## 3. Create the schema

```bash
make migrate
```

That runs `server/migrations/001_schema.sql` against `SUPABASE_DB_URL`. It creates
the call tables (`sessions`, `messages`, `events`, `metrics`), the `profiles` table
and its signup trigger, and the invite-code / waitlist tables. It is **idempotent** —
safe to re-run.

> If `make migrate` can't find `psql`, either install it (`brew install libpq`) or
> paste the file into the Supabase dashboard's SQL editor and hit run.

## 4. Run it

```bash
make build
make run          # http://localhost:8282
make log          # follow it, in another terminal
```

Open the page, enter your email and the invite code **`gradbot`** (seeded by the
migration, valid for 3 days), pick an agent, and allow microphone access.

`make` on its own lists every target.

---

## Accounts

Sign-in is **passwordless and sends no email** — that keeps it under Supabase's
free-tier email quota. You type an email and press *Connect*:

1. The server (holding the `service_role` key) asks Supabase's admin API for a
   one-time `token_hash`. No email leaves the building.
2. The browser redeems that token for a real session.

An email it doesn't recognise needs an **invite code** to create an account;
without one it joins a waitlist. There is no admin UI — codes and admin rights are
managed in SQL:

```sql
-- issue a code
insert into invite_codes (code, max_uses, expires_at)
values ('friends', 20, now() + interval '30 days');

-- make yourself an admin (lets you use personas marked visibility: "admin")
update profiles set is_admin = true where email = 'you@example.com';

-- who's waiting
select * from waitlist order by created_at desc;
```

---

## Who you can call

| | language | voice | LLM |
|---|---|---|---|
| **Sophie** | English | Capucine | Llama 4 Maverick, via OpenRouter |
| **Léo** | French | Vianney | Llama 4 Maverick, via OpenRouter |

Sophie is a French farmer with strong opinions about the price of eggs. Léo is a
discernment guide in the Ignatian tradition — you talk, he listens.

Personas are plain JSON in `personas/`, one file each. They're mounted into the
container, so an edit takes effect on `make restart` with no rebuild:

```json
{
  "agent":   { "active": true, "lang": "fr", "visibility": "public" },
  "persona": { "name": "Leo", "system_prompt_path": "…", "greeting": "Bonjour…" },
  "llm":     { "provider": "openrouter", "model": "meta-llama/llama-4-maverick" },
  "tts":     { "provider": "gradium", "voice_id": "7HhpTMy55D4HkXen" },
  "gradbot": { "silence_timeout_s": 0.0, "assistant_speaks_first": true }
}
```

Any **OpenAI-compatible** LLM endpoint works — OpenRouter, OpenAI, Groq, a local
Ollama. That is the only wire protocol gradbot speaks. Speech is Gradium for both
STT and TTS.

---

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
encoding, jitter-buffered playback — served straight out of the Python package. No
frontend build step, no npm.

## What gets recorded

Each call writes to Postgres as it happens:

- the **transcript**, both sides, one row per turn;
- **turn-taking events** — a `user_stopped_speaking` → `bot_started_speaking` pair
  per turn brackets the silence the caller actually sits through;
- **time-to-first-byte** per stage (`GradbotLLM`, then `GradiumTTS`).

This app has **no dashboard** — it records and never reads back. Point whatever you
like at the database; rows are tagged `framework = 'gradbot'` so several stacks can
share one.

## Known limitations

These come from gradbot itself, and are worth knowing before you build on it:

- **No token accounting.** The Rust core makes the LLM call and never reports
  usage, so `sessions.total_*_tokens` are always zero.
- **No canned opening line.** There is no speak-this-text API, so the agent's first
  turn is a real LLM generation — expect a second or two of cold start before it
  says hello.
- **No hook to observe a session.** Gradbot's WebSocket bridge forwards everything
  to the browser and offers the server nothing, so `server/gradbot_session.py` is a
  fork of it with a tracing hook added. That is why `gradbot` is pinned.

See `CLAUDE.md` for the full engineering notes.
