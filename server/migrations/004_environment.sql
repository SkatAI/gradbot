-- Tag each session with where the server ran: 'local' (dev) or 'online'
-- (deployed). Derived from the DEPLOY_ENV env var in recorder.py. Existing
-- rows predate the column, so they backfill to 'unknown' — we can't know in
-- hindsight where they ran.

ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS environment text NOT NULL DEFAULT 'unknown';
