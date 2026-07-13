-- Sceance observability schema (Supabase / Postgres).
-- Apply with: psql "$SUPABASE_DB_URL" -f server/migrations/001_init.sql

CREATE TABLE IF NOT EXISTS sessions (
    id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    persona_name                  text,
    persona_json                  jsonb,
    lang                          text,
    started_at                    timestamptz NOT NULL DEFAULT now(),
    ended_at                      timestamptz,
    total_prompt_tokens           integer NOT NULL DEFAULT 0,
    total_completion_tokens       integer NOT NULL DEFAULT 0,
    total_cache_read_tokens       integer NOT NULL DEFAULT 0,
    total_cache_creation_tokens   integer NOT NULL DEFAULT 0,
    total_tts_chars               integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role           text NOT NULL,
    text           text NOT NULL,
    language       text,
    stt_timestamp  text,
    recorded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id    uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind          text NOT NULL,
    timestamp_ns  bigint NOT NULL,
    payload       jsonb
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

CREATE TABLE IF NOT EXISTS metrics (
    id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id                    uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    processor                     text NOT NULL,
    model                         text,
    kind                          text NOT NULL,    -- ttfb | processing | llm_usage | tts_usage
    ts                            timestamptz NOT NULL DEFAULT now(),
    value_num                     double precision, -- seconds for ttfb/processing, chars for tts_usage
    prompt_tokens                 integer,
    completion_tokens             integer,
    cache_read_input_tokens       integer,
    cache_creation_input_tokens   integer,
    reasoning_tokens              integer
);
CREATE INDEX IF NOT EXISTS idx_metrics_session ON metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_metrics_kind ON metrics(session_id, kind);
