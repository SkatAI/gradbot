-- Gradbot voice — complete schema. Run once against a fresh Supabase database.
--
--     psql "$SUPABASE_DB_URL" -f server/migrations/001_schema.sql
--     # or: make migrate
--
-- Everything here is idempotent (CREATE ... IF NOT EXISTS, ADD COLUMN IF NOT
-- EXISTS, ON CONFLICT DO NOTHING), so re-running it is a no-op and it is safe to
-- apply to a database that already has some of these objects.
--
-- Requires Supabase Auth to exist (the `auth.users` table). It does on every
-- Supabase project — you do not have to enable anything.
--
-- RLS: none, deliberately. The server connects as the database owner over
-- SUPABASE_DB_URL and bypasses RLS entirely. Nothing else is given credentials,
-- and the browser never talks to Postgres directly — it only ever calls this
-- app's API, which does its own authorization. If you expose this database to
-- anything else, add policies first.


-- ─────────────────────────────────────────────────────────────────────────────
-- Sessions and their traces
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.sessions (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_name                  text,
    -- A point-in-time snapshot of the persona this call ran, with the system
    -- prompt baked in. Persona files get edited; a trace is worthless if you
    -- cannot tell which model and voice produced it.
    persona_json                  jsonb,
    lang                          text,
    started_at                    timestamptz NOT NULL DEFAULT now(),
    ended_at                      timestamptz,
    -- The token totals stay 0 in this app: gradbot's Rust core owns the LLM call
    -- and never reports usage. The columns exist so a shared analytics database
    -- can hold rows from stacks that do report it.
    total_prompt_tokens           integer NOT NULL DEFAULT 0,
    total_completion_tokens       integer NOT NULL DEFAULT 0,
    total_cache_read_tokens       integer NOT NULL DEFAULT 0,
    total_cache_creation_tokens   integer NOT NULL DEFAULT 0,
    total_tts_chars               integer NOT NULL DEFAULT 0,
    -- Where the server ran: 'local' | 'online' | 'unknown' (from DEPLOY_ENV).
    environment                   text NOT NULL DEFAULT 'unknown',
    -- Which voice framework recorded the row. Only matters if you point more than
    -- one stack at the same database and want to tell their traces apart.
    framework                     text NOT NULL DEFAULT 'gradbot'
);
CREATE INDEX IF NOT EXISTS idx_sessions_framework ON public.sessions (framework);

-- The transcript, both sides, one row per turn.
CREATE TABLE IF NOT EXISTS public.messages (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     uuid NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
    role           text NOT NULL,           -- 'user' | 'assistant'
    text           text NOT NULL,
    language       text,
    stt_timestamp  text,
    recorded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON public.messages(session_id);

-- Turn-taking timeline. `timestamp_ns` is a monotonic clock, consistent within a
-- session — all latency maths is a difference of two values from one session, so
-- the origin is arbitrary. Perceived latency is the gap from a
-- 'user_stopped_speaking' event to the next 'bot_started_speaking'.
CREATE TABLE IF NOT EXISTS public.events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id    uuid NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
    kind          text NOT NULL,
    timestamp_ns  bigint NOT NULL,
    payload       jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_session ON public.events(session_id);

-- Per-stage measurements. `processor` names the stage ('GradbotLLM',
-- 'GradiumTTS'); `kind` is ttfb | processing | llm_usage | tts_usage.
CREATE TABLE IF NOT EXISTS public.metrics (
    id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id                    uuid NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
    processor                     text NOT NULL,
    model                         text,
    kind                          text NOT NULL,
    ts                            timestamptz NOT NULL DEFAULT now(),
    value_num                     double precision, -- seconds for ttfb; characters for tts_usage
    prompt_tokens                 integer,
    completion_tokens             integer,
    cache_read_input_tokens       integer,
    cache_creation_input_tokens   integer,
    reasoning_tokens              integer
);
CREATE INDEX IF NOT EXISTS idx_metrics_session ON public.metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_metrics_kind    ON public.metrics(session_id, kind);


-- ─────────────────────────────────────────────────────────────────────────────
-- Accounts
-- ─────────────────────────────────────────────────────────────────────────────

-- App-level profile, 1:1 with Supabase's auth.users.
CREATE TABLE IF NOT EXISTS public.profiles (
    id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username    text NOT NULL UNIQUE,
    email       text NOT NULL,
    is_admin    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Every auth.users insert gets a profile automatically. Username defaults to the
-- email's local part; on a collision a numeric suffix is appended, so a signup
-- never fails outright over a duplicate name.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    base_username text;
    candidate     text;
    suffix        integer := 0;
BEGIN
    base_username := coalesce(
        nullif(new.raw_user_meta_data->>'username', ''),
        split_part(new.email, '@', 1)
    );
    candidate := base_username;
    LOOP
        BEGIN
            INSERT INTO public.profiles (id, username, email)
            VALUES (new.id, candidate, new.email);
            EXIT;
        EXCEPTION WHEN unique_violation THEN
            suffix := suffix + 1;
            candidate := base_username || suffix::text;
            IF suffix > 50 THEN
                RAISE;
            END IF;
        END;
    END LOOP;
    RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Who made the call. Nullable: an anonymous session is still worth recording.
ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON public.sessions(user_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- Signup gate
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Sign-in is passwordless and sends no email (see the readme). An unknown email
-- needs an invite code to create an account; without one it joins the waitlist.
-- Codes are managed by hand — there is no admin UI:
--
--     insert into invite_codes (code, max_uses) values ('friends', 20);
--     update profiles set is_admin = true where email = 'you@example.com';

CREATE TABLE IF NOT EXISTS public.invite_codes (
    code        text PRIMARY KEY,   -- normalized: lowercased, trimmed, [a-z0-9] only
    max_uses    int NOT NULL DEFAULT 5,
    used_count  int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '3 days'
);

CREATE TABLE IF NOT EXISTS public.waitlist (
    email       text PRIMARY KEY,   -- lowercased; the PK gives natural dedupe
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- A first code so you can create your own account. It expires in 3 days —
-- issue yourself another with the insert above if you need one.
INSERT INTO public.invite_codes (code, max_uses) VALUES ('gradbot', 20)
    ON CONFLICT (code) DO NOTHING;
