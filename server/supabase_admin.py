"""Passwordless login via Supabase's GoTrue admin API.

The landing page lets a user sign in with just an email — no password, and
(critically, on the free plan's tight email quota) no magic-link email sent.

To pull that off without emailing, the server holds the Supabase *service-role*
secret and talks to the GoTrue admin REST endpoints directly:

  1. `admin/generate_link` mints a one-time `hashed_token` for an existing user
     WITHOUT delivering an email. The browser redeems it via
     `supabase.auth.verifyOtp({ token_hash, type: 'magiclink' })` to get a real
     session — same JWTs the rest of the app already verifies.
  2. `admin/users` creates a pre-confirmed user when open signup is allowed.

Existence is checked straight against `auth.users` through the existing asyncpg
pool (the SUPABASE_DB_URL role owns the auth schema), so it's deterministic and
independent of GoTrue's autoconfirm/signup settings.

Requires SUPABASE_SERVICE_ROLE_KEY in the environment. This key bypasses RLS and
must never be exposed to the browser — it lives only here, server-side.
"""

from __future__ import annotations

import re

import asyncpg
import httpx
from fastapi import HTTPException
from loguru import logger

from settings import get_settings

# Invite codes are short and case-insensitive; normalize before matching so the
# stored value and the user's input always line up.
_UNSAFE_CODE_CHARS = re.compile(r"[^a-z0-9]")


def normalize_invite_code(code: str) -> str:
    """Lowercase, trim, and strip to a safe charset (a-z0-9)."""
    return _UNSAFE_CODE_CHARS.sub("", (code or "").strip().lower())


def _auth_base() -> str:
    url = get_settings().supabase_url
    return f"{url}/auth/v1" if url else ""


def admin_configured() -> bool:
    return bool(_auth_base() and get_settings().supabase_service_role_key)


def _admin_headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def user_exists(pool: asyncpg.Pool, email: str) -> bool:
    """True if an auth.users row matches this email (case-insensitive)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM auth.users WHERE lower(email) = lower($1) LIMIT 1",
            email,
        )
    return row is not None


async def redeem_invite_code(pool: asyncpg.Pool, code: str) -> bool:
    """Atomically consume one use of an invite code.

    Returns True iff the (normalized) code exists, hasn't expired, and still has
    uses left — incrementing `used_count` in the same statement so concurrent
    signups can't push it past `max_uses`. Blank codes never hit the DB.
    """
    code = normalize_invite_code(code)
    if not code:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.invite_codes
               SET used_count = used_count + 1
             WHERE code = $1 AND used_count < max_uses AND expires_at > now()
            RETURNING code
            """,
            code,
        )
    return row is not None


async def add_to_waitlist(pool: asyncpg.Pool, email: str) -> bool:
    """Record a beta-waitlist signup (idempotent on email).

    Returns True if the email was newly added, False if it was already there.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO public.waitlist (email) VALUES ($1) "
            "ON CONFLICT (email) DO NOTHING RETURNING email",
            email,
        )
    return row is not None


async def create_user(client: httpx.AsyncClient, email: str) -> None:
    """Create a pre-confirmed, passwordless auth.users row.

    `email_confirm: true` skips the confirmation email; the profiles trigger
    fills in a username from the email local-part automatically.
    """
    r = await client.post(
        f"{_auth_base()}/admin/users",
        headers=_admin_headers(),
        json={"email": email, "email_confirm": True},
    )
    if r.status_code >= 400:
        logger.error(f"admin/users create failed ({r.status_code}): {r.text}")
        raise HTTPException(status_code=502, detail="signup_failed")


async def generate_magiclink_token(client: httpx.AsyncClient, email: str) -> str:
    """Mint a one-time login token for an existing user (no email sent).

    Returns the `hashed_token` the browser feeds to verifyOtp. GoTrue versions
    differ on where they put it (top-level vs. nested under `properties`), so we
    check both.
    """
    r = await client.post(
        f"{_auth_base()}/admin/generate_link",
        headers=_admin_headers(),
        json={"type": "magiclink", "email": email},
    )
    if r.status_code >= 400:
        logger.error(f"admin/generate_link failed ({r.status_code}): {r.text}")
        raise HTTPException(status_code=502, detail="login_link_failed")
    data = r.json()
    token = data.get("hashed_token") or data.get("properties", {}).get("hashed_token")
    if not token:
        logger.error(f"generate_link returned no hashed_token: {data}")
        raise HTTPException(status_code=502, detail="login_link_failed")
    return token
