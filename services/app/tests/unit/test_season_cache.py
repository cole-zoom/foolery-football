"""Tests for the per-season cache freshness rules.

We don't hit the network — ``_download`` is monkeypatched to drop a
manifest into the right folder. The point is exercising the
cached/stale/missing decision tree.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ffdm_app import season_cache
from ffdm_app.season_cache import (
    FutureSeasonError,
    ensure_season,
    list_cached_seasons,
)
from ffdm_app.types import LiveState


def _write_manifest(
    root: Path,
    season: int,
    *,
    completed_through_week: int,
    finished_at: datetime | None = None,
) -> None:
    snap = root / str(season)
    snap.mkdir(parents=True, exist_ok=True)
    finished = finished_at or datetime.now(UTC)
    payload = {
        "schema_version": 1,
        "season": season,
        "completed_through_week": completed_through_week,
        "weeks_included": list(range(1, completed_through_week + 1)),
        "upcoming_week_projection": completed_through_week + 1,
        "prior_season_bootstrapped": False,
        "snapshot_started_at": finished.isoformat(),
        "snapshot_finished_at": finished.isoformat(),
        "sources": {},
        "loader_version": "0.1.0",
    }
    (snap / "manifest.json").write_text(json.dumps(payload))
    (snap / "players.json").write_text("{}")


def _stub_download(
    calls: list[tuple[int, int]],
) -> Callable[..., None]:
    def fake(*, season: int, week: int, snapshot_root: Path, sleeper_base_url: str) -> None:
        calls.append((season, week))
        _write_manifest(
            snapshot_root,
            season,
            completed_through_week=min(week - 1, 18),
        )

    return fake


def test_past_season_cached_returns_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_manifest(tmp_path, 2024, completed_through_week=18)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))

    out = ensure_season(
        2024,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
        prefetch_prior=False,
    )

    assert out == tmp_path / "2024"
    assert calls == []  # never re-downloads a past season


def test_past_season_missing_triggers_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))

    ensure_season(
        2023,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
        prefetch_prior=False,
    )

    assert calls == [(2023, season_cache.POST_SEASON_WEEK)]
    assert (tmp_path / "2023" / "manifest.json").exists()


def test_current_season_fresh_returns_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_manifest(tmp_path, 2026, completed_through_week=4)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))

    ensure_season(
        2026,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
        prefetch_prior=False,
    )

    assert calls == []  # cache is fresh


def test_current_season_stale_triggers_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=2)
    _write_manifest(tmp_path, 2026, completed_through_week=4, finished_at=old)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))

    ensure_season(
        2026,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
        prefetch_prior=False,
    )

    assert calls == [(2026, 5)]


def test_current_season_behind_live_triggers_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Cache says week 4 done, live state says week 7 is upcoming (so 6 is done).
    _write_manifest(tmp_path, 2026, completed_through_week=4)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))

    ensure_season(
        2026,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=7),
        prefetch_prior=False,
    )

    assert calls == [(2026, 7)]


def test_future_season_raises(tmp_path: Path) -> None:
    with pytest.raises(FutureSeasonError):
        ensure_season(
            2027,
            snapshot_root=tmp_path,
            sleeper_base_url="https://api.fake",
            live_state=LiveState(season=2026, week=5),
        )


def test_list_cached_seasons_returns_only_complete(tmp_path: Path) -> None:
    _write_manifest(tmp_path, 2023, completed_through_week=18)
    _write_manifest(tmp_path, 2025, completed_through_week=10)
    # Stray dir without manifest — must be ignored.
    (tmp_path / "2024").mkdir()
    # Non-numeric folder — must be ignored.
    (tmp_path / "scratch").mkdir()

    assert list_cached_seasons(tmp_path) == [2023, 2025]


def test_list_cached_seasons_empty_root(tmp_path: Path) -> None:
    assert list_cached_seasons(tmp_path / "absent") == []


def test_prefetch_prior_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Caching a season also fetches the prior one in the background."""

    _write_manifest(tmp_path, 2026, completed_through_week=4)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))
    # Reset dedup so the prefetch actually runs (the module-level set is
    # shared between tests).
    season_cache._prefetch_started.clear()
    # Make the background thread synchronous so the test can assert
    # deterministically without polling.
    monkeypatch.setattr(
        season_cache.threading.Thread,
        "start",
        lambda self: self.run(),
    )

    ensure_season(
        2026,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
    )

    assert calls == [(2025, season_cache.POST_SEASON_WEEK)]
    assert (tmp_path / "2025" / "manifest.json").exists()


def test_prefetch_prior_skipped_when_already_cached(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Don't bother prefetching when the prior season is already on disk."""

    _write_manifest(tmp_path, 2026, completed_through_week=4)
    _write_manifest(tmp_path, 2025, completed_through_week=18)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(season_cache, "_download", _stub_download(calls))
    season_cache._prefetch_started.clear()
    monkeypatch.setattr(
        season_cache.threading.Thread,
        "start",
        lambda self: self.run(),
    )

    ensure_season(
        2026,
        snapshot_root=tmp_path,
        sleeper_base_url="https://api.fake",
        live_state=LiveState(season=2026, week=5),
    )

    assert calls == []
