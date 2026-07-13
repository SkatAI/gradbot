-- User accounts (Supabase Auth + app-level profile).
-- Apply with: psql "$SUPABASE_DB_URL" -f server/migrations/002_auth.sql

CREATE TABLE IF NOT EXISTS public.profiles (
    id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username    text NOT NULL UNIQUE,
    email       text NOT NULL,
    is_admin    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Auto-create a profile row whenever a new auth.users row is inserted.
-- Username comes from raw_user_meta_data.username set by signInWithOtp({data:{username}}).
-- Falls back to the local-part of the email if absent. A retry suffix is appended
-- on unique-constraint collision so signup never fails outright.
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

-- Link sessions to users. Nullable so historical anonymous rows survive.
ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON public.sessions(user_id);
