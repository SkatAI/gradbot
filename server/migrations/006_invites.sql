-- Invite-code gated signup + beta waitlist.
-- Apply with: psql "$SUPABASE_DB_URL" -f server/migrations/006_invites.sql
--
-- Codes are created/managed manually in the Supabase dashboard. The server
-- reaches both tables through the asyncpg pool (direct DB, bypasses RLS), the
-- same way `user_exists` reads auth.users — so no RLS policies are needed.

CREATE TABLE IF NOT EXISTS public.invite_codes (
    code        text PRIMARY KEY,                -- stored normalized: lowercased/trimmed/safe
    max_uses    int NOT NULL DEFAULT 5,
    used_count  int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '3 days'
);

-- People who asked to join without an invite code.
CREATE TABLE IF NOT EXISTS public.waitlist (
    email       text PRIMARY KEY,                -- lowercased; PK gives natural dedupe
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- First invite code.
INSERT INTO public.invite_codes (code) VALUES ('inigo')
    ON CONFLICT (code) DO NOTHING;
