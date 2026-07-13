from __future__ import annotations

import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import pytest  # noqa: E402

import session_tasks  # noqa: E402
from settings import set_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings():
    """Rebuild the settings singleton from env after each test, so a test that
    patches `get_settings()` attributes can't leak into the next one."""
    yield
    set_settings(None)


@pytest.fixture(autouse=True)
def _reset_sessions():
    """The session registry is module-level state; a leaked reservation would
    make the next test's capacity check fail for no visible reason."""
    session_tasks.reset()
    yield
    session_tasks.reset()


class FakeRecorder:
    """Captures what a SessionTracer writes, without a database.

    Mirrors SessionRecorder's three record_* methods; the tracer only ever calls
    those.
    """

    def __init__(self):
        self.messages: list[dict] = []
        self.events: list[dict] = []
        self.metrics: list[dict] = []

    def record_message(self, role, text, language=None, stt_timestamp=None):
        self.messages.append({"role": role, "text": text, "language": language})

    def record_event(self, kind, timestamp_ns, payload=None):
        self.events.append({"kind": kind, "timestamp_ns": timestamp_ns, "payload": payload})

    def record_metric(self, processor, model, kind, value_num=None, **kwargs):
        self.metrics.append({
            "processor": processor, "model": model, "kind": kind, "value_num": value_num,
        })

    def event_kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)
