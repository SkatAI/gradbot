-- 007: tag each session with the voice framework that produced it.
--
-- The gradbot app and the pipecat app (sceance) share one Supabase database, so
-- every consumer of `sessions` needs to be able to tell the two apart. The
-- DEFAULT does the backfill: every pre-existing row was written by pipecat, and
-- sceance's INSERT (which does not list this column) keeps working untouched.
-- The gradbot app passes 'gradbot' explicitly.
--
-- Apply once against the shared DB:
--     psql "$SUPABASE_DB_URL" -f server/migrations/007_framework.sql

alter table public.sessions
    add column if not exists framework text not null default 'pipecat';

create index if not exists idx_sessions_framework on public.sessions (framework);
