"""Shared FastAPI dependencies for route modules."""

from __future__ import annotations

from starlette.requests import HTTPConnection


def get_pool(conn: HTTPConnection):
    """The asyncpg pool, created in the app lifespan and stored on app state.

    Typed as HTTPConnection (the common base of Request and WebSocket) rather
    than Request, because `/ws/chat` needs the pool too and a WebSocket route
    cannot depend on a Request.

    Injected into handlers so they stay directly unit-testable: a test calls the
    handler with a fake pool instead of standing up the app.
    """
    return conn.app.state.db_pool
