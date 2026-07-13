"""Registry of voice sessions — reserved, then active.

Shared by `/health` (reports the count), `/start-session` (reserves a slot and
enforces MAX_SESSIONS), and `/ws/chat` (claims the reservation, then holds the
slot for the life of the connection).

Two-phase, because a gradbot session is split across two requests: it only really
begins when the browser opens the WebSocket. So `/start-session` mints a session
id and *reserves* it, and the socket *claims* it moments later.

That reservation is also what stops a client from choosing its own session id —
which would otherwise let it collide with, or impersonate, someone else's row.
Only ids this server minted can be claimed, and only once.

There is no background task to cancel on shutdown: a session *is* its WebSocket
handler, and those close on their own when the server stops.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

# A reservation the browser never redeemed (it closed the tab between POST and
# WebSocket) must not hold a slot forever.
RESERVATION_TTL_S = 60.0


@dataclass(frozen=True)
class Reservation:
    session_id: uuid.UUID
    agent: str
    user_id: str
    created_at: float


_reserved: dict[str, Reservation] = {}
_active: set[str] = set()


def _prune(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for sid, r in list(_reserved.items()):
        if now - r.created_at > RESERVATION_TTL_S:
            del _reserved[sid]


def count() -> int:
    """Slots in use: live calls plus reservations about to become live calls."""
    _prune()
    return len(_active) + len(_reserved)


def reserve(agent: str, user_id: str) -> Reservation:
    """Mint a session id and hold a slot for it."""
    _prune()
    reservation = Reservation(
        session_id=uuid.uuid4(),
        agent=agent,
        user_id=user_id,
        created_at=time.monotonic(),
    )
    _reserved[str(reservation.session_id)] = reservation
    return reservation


def claim(session_id: str) -> Reservation | None:
    """Redeem a reservation and promote it to an active session.

    Returns None if the id is unknown, expired, or already claimed. The slot is
    handed straight from `_reserved` to `_active` so `count()` never dips in
    between — otherwise a burst of connects could slip past MAX_SESSIONS.
    """
    _prune()
    reservation = _reserved.pop(session_id, None)
    if reservation is None:
        return None
    _active.add(session_id)
    return reservation


def release(session_id: str) -> None:
    """Free the slot once the WebSocket handler is done."""
    _active.discard(session_id)


def reset() -> None:
    """Drop all bookkeeping (tests)."""
    _reserved.clear()
    _active.clear()
