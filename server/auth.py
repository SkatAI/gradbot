"""Supabase JWT verification and per-request user lookup.

The browser uses @supabase/supabase-js for magic-link sign-in. It sends the
access_token as `Authorization: Bearer <jwt>`. We validate the token against
Supabase's JWKS, then look up the matching `profiles` row to get the username
and admin flag the app needs.

JWKS is fetched once on first use and cached. Supabase rotates keys rarely;
restart the process if you ever need to force a refresh.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import jwt
from fastapi import Header, HTTPException, Request
from jwt import PyJWKClient
from loguru import logger

from settings import get_settings

_AUDIENCE = "authenticated"

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient | None:
    """Lazy JWKS client. Returns None if SUPABASE_URL isn't set or JWKS is empty
    (older Supabase projects sign with the legacy HS256 shared secret instead)."""
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    supabase_url = get_settings().supabase_url
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json" if supabase_url else ""
    if not jwks_url:
        return None
    try:
        r = httpx.get(jwks_url, timeout=5.0)
        r.raise_for_status()
        if not r.json().get("keys"):
            return None
    except httpx.HTTPError as e:
        logger.warning(f"Could not fetch Supabase JWKS at {jwks_url}: {e}")
        return None
    _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def verify_jwt(token: str) -> dict:
    """Validate a Supabase access token. Raises 401 on any failure."""
    client = _get_jwks_client()
    try:
        if client is not None:
            signing_key = client.get_signing_key_from_jwt(token).key
            return jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=_AUDIENCE,
            )
        jwt_secret = get_settings().supabase_jwt_secret
        if not jwt_secret:
            raise RuntimeError("no JWKS available and SUPABASE_JWT_SECRET unset")
        return jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience=_AUDIENCE,
        )
    except jwt.PyJWTError as e:
        # Log the reason. Without this a 401 is a dead end: the detail only
        # reaches the browser's network tab, so the server log shows a bare
        # "401 Unauthorized" and you cannot tell an expired token from a bad
        # signature from the wrong audience.
        logger.warning(f"JWT rejected ({type(e).__name__}): {e}{_token_hint(token)}")
        raise HTTPException(status_code=401, detail=f"invalid_token: {e}") from e
    except Exception as e:
        logger.exception("JWT verify failed")
        raise HTTPException(status_code=401, detail="auth_unavailable") from e


def _token_hint(token: str) -> str:
    """Un-verified claims, for the log line only — never for an auth decision."""
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, options={"verify_signature": False})
        return (
            f" [alg={header.get('alg')} aud={claims.get('aud')} "
            f"iss={claims.get('iss')} exp={claims.get('exp')}]"
        )
    except Exception:
        return " [token is not a readable JWT]"


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    username: str
    is_admin: bool


async def user_from_token(pool, token: str) -> CurrentUser:
    """Verify an access token and load its profile.

    Split out of `get_current_user` because the WebSocket handshake has no
    request and no headers to depend on — `/ws/chat` carries the token in the
    start message and calls this directly.
    """
    claims = verify_jwt(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="token_missing_sub")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, username, is_admin FROM profiles WHERE id = $1",
            user_id,
        )
    if row is None:
        raise HTTPException(status_code=403, detail="profile_missing")
    return CurrentUser(
        id=str(row["id"]),
        email=row["email"],
        username=row["username"],
        is_admin=bool(row["is_admin"]),
    )


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    token = authorization.split(" ", 1)[1].strip()
    return await user_from_token(request.app.state.db_pool, token)


async def get_current_user_optional(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CurrentUser | None:
    """Like get_current_user, but returns None instead of raising when no/invalid
    token is present — for routes that serve everyone but tailor by admin status."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        return await get_current_user(request, authorization)
    except HTTPException:
        return None


# There is no `require_admin` dependency: this app exposes no admin API.
# `is_admin` survives on CurrentUser only because a persona can be marked
# `visibility: "admin"`, which /agents and /start-session check directly.
