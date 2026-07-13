"""Authentication routes: passwordless email login and current-user lookup.

`from auth import ...` is an absolute import — it resolves to the top-level
`auth.py` (JWT verification), not this module.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import CurrentUser, get_current_user
from deps import get_pool
from supabase_admin import (
    add_to_waitlist,
    admin_configured,
    create_user,
    generate_magiclink_token,
    redeem_invite_code,
    user_exists,
)

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    invite_code: str | None = None


@router.post("/api/auth/login")
async def api_auth_login(body: LoginBody, pool=Depends(get_pool)):
    """Passwordless sign-in by email — no magic-link email is sent.

    Returns `{status: "ok", token_hash, type}` the browser redeems with verifyOtp.

    New emails are gated by an invite code: a valid code creates the account and
    logs in immediately. Otherwise the email joins the beta waitlist and the
    response reports `{status: "waitlisted", already, invalid_code}` — `already`
    if the email was on the list, `invalid_code` if a code was given but didn't
    work (so the page can say "that code wasn't valid, but you're on the list").
    """
    email = (body.email or "").strip().lower()
    if "@" not in email or len(email) < 3:
        raise HTTPException(status_code=400, detail="invalid_email")
    if not admin_configured():
        raise HTTPException(status_code=500, detail="auth_admin_not_configured")

    if not await user_exists(pool, email):
        code = (body.invite_code or "").strip()
        if code and await redeem_invite_code(pool, body.invite_code):
            async with httpx.AsyncClient(timeout=15.0) as client:
                await create_user(client, email)
                return {
                    "status": "ok",
                    "token_hash": await generate_magiclink_token(client, email),
                    "type": "magiclink",
                }
        added = await add_to_waitlist(pool, email)
        return {"status": "waitlisted", "already": not added, "invalid_code": bool(code)}

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_hash = await generate_magiclink_token(client, email)
    return {"status": "ok", "token_hash": token_hash, "type": "magiclink"}


@router.get("/api/me")
async def api_me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "is_admin": user.is_admin,
    }
