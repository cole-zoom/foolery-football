"""Tests for core.pipeline.run using FakeHttp + FakeWriter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from stats_loader.clients.http import HttpError, NotFoundError
from stats_loader.core import pipeline
from stats_loader.types import NflState
from tests.unit.fakes import FakeHttp, FakeWriter, make_players, make_schedule, make_weekly


def _now() -> datetime:
    return datetime(2026, 9, 15, 8, 0, 0, tzinfo=UTC)


def _routes_for_state(season: int, week: int) -> dict[str, Any]:
    """Routes covering players + state + every past week + the upcoming week."""

    players = make_players()
    pids = list(players.keys())[:50]
    routes: dict[str, Any] = {
        "/v1/state/nfl": {"season": season, "week": week},
        "/v1/players/nfl": players,
        f"/schedule/nfl/regular/{season}": make_schedule(),
    }
    for w in range(1, week):
        routes[f"/v1/stats/nfl/regular/{season}/{w}"] = make_weekly(pids)
        routes[f"/v1/projections/nfl/regular/{season}/{w}"] = make_weekly(pids)
    if week >= 1:
        routes[f"/v1/projections/nfl/regular/{season}/{week}"] = make_weekly(pids)
    return routes


def test_midseason_run_writes_expected_artifacts() -> None:
    http = FakeHttp(_routes_for_state(2026, 5))
    writer = FakeWriter()

    result = pipeline.run(
        http=http,
        writer_factory=lambda _season: writer,
        state_override=None,
        now=_now(),
        dry_run=False,
    )

    assert "players.json" in writer.artifacts
    assert "schedule.json" in writer.artifacts
    assert writer.committed_manifest is not None
    assert "schedule" in writer.committed_manifest["sources"]  # type: ignore[operator]
    for w in range(1, 5):
        assert f"stats_week_{w}.json" in writer.artifacts
        assert f"projections_week_{w}.json" in writer.artifacts
    # Upcoming-week projection for week 5 also present.
    assert "projections_week_5.json" in writer.artifacts
    assert writer.committed_manifest is not None
    assert writer.committed_manifest["season"] == 2026
    assert writer.committed_manifest["completed_through_week"] == 4
    assert writer.committed_manifest["upcoming_week_projection"] == 5
    assert writer.committed_manifest["prior_season_bootstrapped"] is False
    assert result.snapshot_path == writer.commit_path


def test_state_override_skips_state_call() -> None:
    routes = _routes_for_state(2025, 3)
    # Remove the state endpoint so the test fails if pipeline calls it.
    del routes["/v1/state/nfl"]
    http = FakeHttp(routes)
    writer = FakeWriter()

    pipeline.run(
        http=http,
        writer_factory=lambda _season: writer,
        state_override=NflState(season=2025, week=3),
        now=_now(),
        dry_run=False,
    )

    assert "/v1/state/nfl" not in http.calls
    assert "stats_week_2.json" in writer.artifacts


def test_dry_run_does_not_call_writer() -> None:
    http = FakeHttp(_routes_for_state(2026, 3))

    result = pipeline.run(
        http=http,
        writer_factory=None,
        state_override=None,
        now=_now(),
        dry_run=True,
    )

    assert result.snapshot_path is None
    assert result.dry_run is True
    # We still fetched and validated everything.
    assert "/v1/players/nfl" in http.calls
    assert "/v1/stats/nfl/regular/2026/2" in http.calls


def test_week_1_bootstraps_prior_season() -> None:
    routes = _routes_for_state(2026, 1)
    pids = list(make_players().keys())[:50]
    routes["/v1/stats/nfl/regular/2025"] = make_weekly(pids)
    http = FakeHttp(routes)
    writer = FakeWriter()

    pipeline.run(
        http=http,
        writer_factory=lambda _season: writer,
        state_override=None,
        now=_now(),
        dry_run=False,
    )

    assert "stats_prior_season.json" in writer.artifacts
    assert writer.committed_manifest is not None
    assert writer.committed_manifest["prior_season_bootstrapped"] is True


def test_week_1_tolerates_missing_prior_season() -> None:
    """If the prior-season endpoint 404s, we proceed without bootstrap."""

    routes = _routes_for_state(2026, 1)
    routes["/v1/stats/nfl/regular/2025"] = NotFoundError("404")
    http = FakeHttp(routes)
    writer = FakeWriter()

    pipeline.run(
        http=http,
        writer_factory=lambda _season: writer,
        state_override=None,
        now=_now(),
        dry_run=False,
    )

    assert "stats_prior_season.json" not in writer.artifacts


def test_missing_schedule_is_a_soft_miss() -> None:
    """A 404 on the schedule endpoint must not fail the whole run."""

    routes = _routes_for_state(2026, 3)
    routes["/schedule/nfl/regular/2026"] = NotFoundError("404")
    http = FakeHttp(routes)
    writer = FakeWriter()

    pipeline.run(
        http=http,
        writer_factory=lambda _season: writer,
        state_override=None,
        now=_now(),
        dry_run=False,
    )

    assert "schedule.json" not in writer.artifacts
    assert writer.committed_manifest is not None
    assert "schedule" not in writer.committed_manifest["sources"]  # type: ignore[operator]


def test_pipeline_aborts_on_http_error_for_past_week() -> None:
    routes = _routes_for_state(2026, 3)
    routes["/v1/stats/nfl/regular/2026/2"] = HttpError("503 after 3 attempts")
    http = FakeHttp(routes)
    writer = FakeWriter()

    with pytest.raises(HttpError):
        pipeline.run(
            http=http,
            writer_factory=lambda _season: writer,
            state_override=None,
            now=_now(),
            dry_run=False,
        )

    # We may have written some artifacts before the failure, but commit
    # was never called — that's the atomic guarantee.
    assert writer.committed_manifest is None
