-- User memory across sessions.
-- Apply with: psql "$SUPABASE_DB_URL" -f server/migrations/003_user_memory.sql

CREATE TABLE IF NOT EXISTS public.user_profile (
    user_id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    profile_text     text NOT NULL DEFAULT '',
    updated_at       timestamptz NOT NULL DEFAULT now(),
    last_session_id  uuid REFERENCES public.sessions(id) ON DELETE SET NULL,
    summary_model    text
);

ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS summary       text,
    ADD COLUMN IF NOT EXISTS summary_model text,
    ADD COLUMN IF NOT EXISTS summarized_at timestamptz;
