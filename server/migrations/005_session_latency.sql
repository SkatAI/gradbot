-- Per-session latency analysis: one stored analysis per session, generated on
-- demand from the operator dashboard ("Latency analysis" button). The
-- deterministic collector output (`bundle`) and the LLM synthesis (`report`)
-- are both kept so the page can re-render without recomputing. Single row per
-- session — regenerating overwrites (ON CONFLICT (session_id) DO UPDATE).
--
-- Apply with: psql "$SUPABASE_DB_URL" -f server/migrations/005_session_latency.sql

CREATE TABLE IF NOT EXISTS public.session_latency_analysis (
    session_id      uuid PRIMARY KEY REFERENCES public.sessions(id) ON DELETE CASCADE,
    bundle          jsonb NOT NULL,                 -- deterministic collector output
    report          jsonb NOT NULL,                 -- LLM synthesis output (structured)
    has_unexplained boolean NOT NULL DEFAULT false, -- model flagged something outside known buckets
    model           text,                           -- synthesis model id
    generated_at    timestamptz NOT NULL DEFAULT now()
);
