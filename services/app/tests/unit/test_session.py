"""Tests for the session API.

These confirm the orchestration logic (cache ensure -> decide call) without
hitting Sleeper. ``session.decide`` is the same API a web frontend will
call, so the contract matters.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ffdm_app import session as session_mod
from ffdm_app.session import (
    available_seasons,
    default_week_for_season,
)
from ffdm_app.types import LiveState


def _write_manifest(root: Path, season: int, completed_through_week: int) -> None:
    snap = root / str(season)
    snap.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "season": season,
                "completed_through_week": completed_through_week,
                "weeks_included": list(range(1, completed_through_week + 1)),
                "upcoming_week_projection": completed_through_week + 1,
                "prior_season_bootstrapped": False,
                "snapshot_started_at": now,
                "snapshot_finished_at": now,
                "sources": {},
                "loader_version": "0.1.0",
            }
        )
    )


def test_available_seasons_marks_cached_and_uncached(tmp_path: Path) -> None:
    _write_manifest(tmp_path, 2025, completed_through_week=18)
    _write_manifest(tmp_path, 2023, completed_through_week=18)

    out = available_seasons(
        snapshot_root=tmp_path,
        live_state=LiveState(season=2026, week=5),
        history_years=3,
    )

    by_season = {info.season: info for info in out}
    assert set(by_season.keys()) == {2026, 2025, 2024, 2023}
    assert by_season[2026].cached is False
    assert by_season[2025].cached is True
    assert by_season[2024].cached is False
    assert by_season[2023].cached is True
    assert by_season[2025].completed_through_week == 18
    assert by_season[2024].completed_through_week is None


def test_available_seasons_listed_newest_first(tmp_path: Path) -> None:
    out = available_seasons(
        snapshot_root=tmp_path,
        live_state=LiveState(season=2026, week=5),
        history_years=2,
    )
    assert [info.season for info in out] == [2026, 2025, 2024]


def test_default_season_offseason_is_previous() -> None:
    # Sleeper has flipped to 2026 but no games played — default to 2025.
    assert session_mod.default_season(LiveState(season=2026, week=0)) == 2025
    assert session_mod.default_season(LiveState(season=2026, week=1)) == 2025


def test_default_season_active_season_is_live() -> None:
    # At least one completed week — current season is the right default.
    assert session_mod.default_season(LiveState(season=2026, week=2)) == 2026
    assert session_mod.default_season(LiveState(season=2026, week=10)) == 2026


def test_default_week_past_season_is_18() -> None:
    assert default_week_for_season(2024, live_state=LiveState(season=2026, week=5)) == 18


def test_default_week_current_season_is_last_completed() -> None:
    # Live state week 7 means weeks 1..6 are done; default replay is week 6.
    assert default_week_for_season(2026, live_state=LiveState(season=2026, week=7)) == 6


def test_default_week_current_season_clamps_to_1() -> None:
    # Week 1 is upcoming — no games done yet. Don't return 0.
    assert default_week_for_season(2026, live_state=LiveState(season=2026, week=1)) == 1


def test_default_week_current_season_caps_at_18() -> None:
    # Postseason: live state week stays at 19; cap at 18.
    assert default_week_for_season(2026, live_state=LiveState(season=2026, week=19)) == 18


def test_default_snapshot_root_points_into_repo() -> None:
    """Path arithmetic in default_snapshot_root regressed once; pin it."""

    root = session_mod.default_snapshot_root()
    assert root.parts[-2:] == ("data", "seasons")
    # The repo root (parent of ``data``) must contain ``services`` — that's
    # the anchor that tells us the walk-up landed at the right depth.
    assert (root.parent.parent / "services").is_dir()


def test_fetch_live_state_uses_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_live_state delegates to stats-loader's validator. Smoke-check."""

    class _FakeHttp:
        def __init__(self, *a: object, **kw: object) -> None: ...
        def __enter__(self) -> _FakeHttp:
            return self

        def __exit__(self, *a: object) -> None: ...
        def get_json(self, path: str) -> object:
            assert path == "/v1/state/nfl"
            return {"season": 2026, "week": 7}

    monkeypatch.setattr(session_mod, "LoaderHttpClient", _FakeHttp)
    state = session_mod.fetch_live_state(sleeper_base_url="https://api.fake")
    assert state == LiveState(season=2026, week=7)
