"""Tests for SleeperHttpClient retry/backoff behaviour."""

from __future__ import annotations

import httpx
import pytest

from stats_loader.clients.http import (
    HttpError,
    NotFoundError,
    SleeperHttpClient,
)


def _client(handler: httpx.MockTransport, sleeps: list[float]) -> SleeperHttpClient:
    return SleeperHttpClient(
        "https://api.fake",
        client=httpx.Client(transport=handler),
        sleep=sleeps.append,
    )


def test_200_returns_parsed_json() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    sleeps: list[float] = []
    assert _client(transport, sleeps).get_json("/v1/state/nfl") == {"ok": True}
    assert sleeps == []


def test_429_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    assert _client(httpx.MockTransport(handler), sleeps).get_json("/x") == {"ok": True}
    assert calls["n"] == 3
    # Two retries -> two sleeps (1s, 2s).
    assert sleeps == [1.0, 2.0]


def test_5xx_exhausts_retries_then_raises() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(503, text="down"))
    sleeps: list[float] = []
    with pytest.raises(HttpError, match="after 3 attempts"):
        _client(transport, sleeps).get_json("/x")


def test_404_raises_not_found_without_retry() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="missing"))
    sleeps: list[float] = []
    with pytest.raises(NotFoundError):
        _client(transport, sleeps).get_json("/x")
    assert sleeps == []


def test_other_4xx_is_fatal_without_retry() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad"))
    sleeps: list[float] = []
    with pytest.raises(HttpError, match="non-retryable"):
        _client(transport, sleeps).get_json("/x")
    assert sleeps == []


def test_unparseable_json_is_fatal() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
    )
    sleeps: list[float] = []
    with pytest.raises(HttpError, match="not parseable as JSON"):
        _client(transport, sleeps).get_json("/x")
