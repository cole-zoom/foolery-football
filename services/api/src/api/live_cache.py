"""Short-TTL caches for live Sleeper reads.

Every decisions/comparison request opens with the same chain of Sleeper
calls — ``/state/nfl`` plus the four-step league-context resolution —
and none of that changes within a browsing session. Caching them for
:data:`TTL_SECONDS` turns the 17-week prefetch fan-out from ~85 external
round trips into ~5.

Process-local by design, like the snapshot cache: Cloud Run instances
each warm their own. Only successes are cached — a Sleeper hiccup or an
unknown username must stay retryable immediately.

The cached pydantic models are shared across requests/threads; callers
treat them as read-only (``model_copy`` for mutation, as /comparison
already does).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from decision_engine.clients.http import HttpClient
from decision_engine.core.league_fetch import fetch_league_context, resolve_state
from decision_engine.types import LeagueContext, NflState

TTL_SECONDS = 60.0

T = TypeVar("T")


class _TtlCache:
    """Tiny thread-safe TTL cache. Concurrent misses may compute twice;
    the results are identical and the race is benign."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[object, tuple[float, object]] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_compute(self, key: object, compute: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                return hit[1]  # type: ignore[return-value]
        value = compute()
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            # Opportunistic sweep so dead keys (old seasons, typo'd
            # usernames that later succeeded) don't accumulate forever.
            if len(self._entries) > 256:
                cutoff = time.monotonic() - self._ttl
                for k in [k for k, (t, _) in self._entries.items() if t < cutoff]:
                    del self._entries[k]
        return value


_state_cache = _TtlCache(TTL_SECONDS)
_context_cache = _TtlCache(TTL_SECONDS)


def clear() -> None:
    """Drop everything. For tests — each test fakes its own Sleeper."""

    _state_cache.clear()
    _context_cache.clear()


def get_state(http: HttpClient) -> NflState:
    """Cached ``resolve_state`` — live NFL season/week."""

    return _state_cache.get_or_compute("nfl", lambda: resolve_state(http, None))


def get_league_context(
    http: HttpClient,
    *,
    username: str,
    league_id: str,
    season: int,
) -> LeagueContext:
    """Cached ``fetch_league_context`` — user/league/rosters resolution."""

    return _context_cache.get_or_compute(
        (username, league_id, season),
        lambda: fetch_league_context(
            http, username=username, league_id=league_id, season=season
        ),
    )
