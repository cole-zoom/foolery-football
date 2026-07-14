"""Shared plumbing for the eval harness.

``CachedSleeperHttp`` implements decision-engine's ``HttpClient``
protocol with a write-through disk cache: every Sleeper response lands
in ``evals/cache/`` keyed by URL path, so re-runs (and the eval driver
after discovery) are fully offline. Live misses are throttled to stay
politely under Sleeper's ~1000 req/min limit.

``qualifies`` is the pure league filter the crawler applies; it lives
here so a fixture test can exercise it without any network.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from decision_engine.clients.http import (
    HttpClient,
    NotFoundError,
    SleeperHttpClient,
)
from decision_engine.core.eligibility import NON_SELECTABLE_SLOTS, SLOT_ELIGIBILITY
from decision_engine.core.replay import PERFECT_LINEUP_MAX_SLOTS

SLEEPER_BASE_URL = "https://api.sleeper.app"
DEFAULT_CACHE_DIR = Path(__file__).parent / "cache"

# Sentinel body cached for 404s so re-runs don't re-fetch known misses.
_NOT_FOUND_SENTINEL = {"__not_found__": True}


class CachedSleeperHttp:
    """Disk-caching, throttled ``HttpClient`` over the real Sleeper API."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        *,
        min_interval_s: float = 0.1,
        base_url: str = SLEEPER_BASE_URL,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._min_interval_s = min_interval_s
        self._inner = SleeperHttpClient(base_url)
        self._last_fetch = 0.0
        self.live_calls = 0
        self.cache_hits = 0

    def get_json(self, path: str) -> object:
        cache_path = self._cache_dir / (path.strip("/").replace("/", "_") + ".json")
        if cache_path.exists():
            self.cache_hits += 1
            payload = json.loads(cache_path.read_text())
            if payload == _NOT_FOUND_SENTINEL:
                raise NotFoundError(f"{path}: 404 (cached)")
            return payload

        wait = self._min_interval_s - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch = time.monotonic()
        self.live_calls += 1
        try:
            payload = self._inner.get_json(path)
        except NotFoundError:
            _atomic_write_json(cache_path, _NOT_FOUND_SENTINEL)
            raise
        _atomic_write_json(cache_path, payload)
        return payload

    def close(self) -> None:
        self._inner.close()


# Statically prove CachedSleeperHttp satisfies the engine's protocol.
_: type[HttpClient] = CachedSleeperHttp


def _atomic_write_json(path: Path, payload: object) -> None:
    # PID-unique tmp name: two harness processes sharing the cache must
    # never interleave writes into the same tmp file.
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def write_json(path: Path, payload: object, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=indent) + "\n")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def parse_weeks(spec: str) -> list[int]:
    """``"1-18"`` or ``"3,5,7"`` or ``"1-4,10"`` -> sorted week list."""

    weeks: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            weeks.update(range(int(lo), int(hi) + 1))
        else:
            weeks.add(int(part))
    return sorted(weeks)


_KNOWN_SLOTS = frozenset(SLOT_ELIGIBILITY) | NON_SELECTABLE_SLOTS


def qualifies(raw: object, *, season: int) -> tuple[bool, str]:
    """Can this raw ``/v1/league/<id>`` payload be evaluated? -> (ok, reason).

    Mirrors what the replay can actually handle: completed NFL redraft
    league, human-managed lineups (no best ball), only slots the
    eligibility map knows (rejects IDP), and few enough starter slots
    that the perfect-hindsight DP runs.
    """

    if not isinstance(raw, dict):
        return False, "not a league object"
    if raw.get("sport") != "nfl":
        return False, f"sport={raw.get('sport')!r}"
    if raw.get("season") != str(season):
        return False, f"season={raw.get('season')!r}"
    if raw.get("status") != "complete":
        return False, f"status={raw.get('status')!r}"

    settings = raw.get("settings") or {}
    if settings.get("best_ball") == 1:
        return False, "best ball"

    roster_positions = raw.get("roster_positions") or []
    if not roster_positions:
        return False, "no roster_positions"
    unknown = sorted({s for s in roster_positions if s not in _KNOWN_SLOTS})
    if unknown:
        return False, f"unsupported slots {unknown}"
    selectable = [s for s in roster_positions if s not in NON_SELECTABLE_SLOTS]
    if not selectable:
        return False, "no selectable slots"
    if len(selectable) > PERFECT_LINEUP_MAX_SLOTS:
        return False, f"{len(selectable)} starter slots (> {PERFECT_LINEUP_MAX_SLOTS})"

    if not raw.get("scoring_settings"):
        return False, "no scoring_settings"

    return True, "ok"


def scoring_kind(raw_league: dict[str, Any]) -> str:
    """Coarse label for reports: ppr / half_ppr / standard / custom."""

    rec = (raw_league.get("scoring_settings") or {}).get("rec", 0.0)
    if rec == 1.0:
        return "ppr"
    if rec == 0.5:
        return "half_ppr"
    if not rec:
        return "standard"
    return "custom"


def full_starter_lineup(starters: object) -> bool:
    """True when every starter slot was filled by a real player.

    Sleeper marks an empty starting slot with the string ``"0"``; a
    manager who left slots empty (or a matchup with no starters at all)
    fails the check.
    """

    if not isinstance(starters, list) or not starters:
        return False
    return all(isinstance(s, str) and s not in ("", "0") for s in starters)
