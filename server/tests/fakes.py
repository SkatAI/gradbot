"""Shared test fakes for the Daily REST client.

``FakeDailyClient`` stands in for an ``httpx.AsyncClient`` and records the
payloads posted to the Daily room / meeting-token endpoints. Used by both the
``daily_client`` unit tests and the ``start_session`` integration test.
"""

from __future__ import annotations

import httpx


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad response",
                request=httpx.Request("POST", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.data


class FakeDailyClient:
    def __init__(self):
        self.posts = []

    async def post(self, url, json):
        self.posts.append((url, json))
        if url.endswith("/rooms"):
            return FakeResponse({"name": "room-a", "url": "https://room.test/a"})
        if url.endswith("/meeting-tokens"):
            owner = json["properties"]["is_owner"]
            return FakeResponse({"token": "bot-token" if owner else "user-token"})
        raise AssertionError(url)
