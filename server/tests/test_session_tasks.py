"""The two-phase session registry.

A gradbot session spans two requests (POST then WebSocket), so a slot has to be
held across the gap. The interesting cases are all about that gap.
"""

from __future__ import annotations

import session_tasks


def test_reservation_holds_a_slot_before_the_socket_connects():
    # Otherwise MAX_SESSIONS could be blown through by N clients that have all
    # POSTed but not yet dialled.
    session_tasks.reserve("yarden_mini", "user-1")
    assert session_tasks.count() == 1


def test_claiming_promotes_the_slot_without_ever_releasing_it():
    r = session_tasks.reserve("yarden_mini", "user-1")
    assert session_tasks.count() == 1

    claimed = session_tasks.claim(str(r.session_id))

    assert claimed is not None
    assert claimed.agent == "yarden_mini"
    assert claimed.user_id == "user-1"
    # The slot moved reserved -> active. If count() dipped to 0 here, a burst of
    # connects could slip past the capacity check.
    assert session_tasks.count() == 1


def test_releasing_frees_the_slot():
    r = session_tasks.reserve("yarden_mini", "user-1")
    session_tasks.claim(str(r.session_id))
    session_tasks.release(str(r.session_id))
    assert session_tasks.count() == 0


def test_a_session_id_cannot_be_claimed_twice():
    r = session_tasks.reserve("yarden_mini", "user-1")
    assert session_tasks.claim(str(r.session_id)) is not None
    assert session_tasks.claim(str(r.session_id)) is None


def test_an_unminted_session_id_cannot_be_claimed():
    # This is what stops a client picking its own session id and writing rows
    # against someone else's — or a colliding — session.
    assert session_tasks.claim("00000000-0000-0000-0000-000000000000") is None


def test_abandoned_reservations_expire_instead_of_leaking_a_slot():
    # The browser closed the tab between POST and WebSocket. Without a TTL that
    # slot is gone until restart.
    r = session_tasks.reserve("yarden_mini", "user-1")
    assert session_tasks.count() == 1

    session_tasks._reserved[str(r.session_id)] = session_tasks.Reservation(
        session_id=r.session_id,
        agent=r.agent,
        user_id=r.user_id,
        created_at=r.created_at - session_tasks.RESERVATION_TTL_S - 1,
    )

    assert session_tasks.count() == 0
    assert session_tasks.claim(str(r.session_id)) is None


def test_each_reservation_gets_its_own_id():
    a = session_tasks.reserve("yarden_mini", "user-1")
    b = session_tasks.reserve("yarden_mini", "user-1")
    assert a.session_id != b.session_id
    assert session_tasks.count() == 2
