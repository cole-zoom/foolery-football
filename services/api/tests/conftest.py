"""Shared fixtures for the api test suite.

The api package is thin: routers translate query params, call into
decision-engine, shape responses. The hardest things to fake are the
``SleeperHttpClient`` (HTTP) and ``CachingSnapshotReader`` (disk/GCS).
``conftest.py`` provides minimal in-memory stand-ins plus a
``TestClient`` factory that wires them through FastAPI dependency
overrides.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from decision_engine.types import Player, SnapshotData
from fastapi.testclient import TestClient

from api.config import Settings
from api.deps import (
    PrepareSeason,
    get_http_client,
    get_prepare_season,
    get_settings,
    get_snapshot_reader,
)
from api.main import create_app


class FakeHttp:
    """In-memory ``SleeperHttpClient``. ``routes`` maps path -> payload or exception."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = dict(routes)
        self.calls: list[str] = []

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        if path not in self._routes:
            raise AssertionError(f"FakeHttp: unexpected GET {path}")
        value = self._routes[path]
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:  # match SleeperHttpClient interface
        return None


class FakeSnapshotReader:
    """In-memory ``SnapshotReader``. Returns prebuilt snapshots keyed by season."""

    def __init__(self, snapshots: dict[int, SnapshotData]) -> None:
        self._snapshots = snapshots

    def load(self, season: int) -> SnapshotData:
        from decision_engine.clients.snapshot_reader import SnapshotMissingError

        if season not in self._snapshots:
            raise SnapshotMissingError(f"no fake snapshot for season {season}")
        return self._snapshots[season]


def make_player(
    pid: str,
    *,
    full_name: str = "Player X",
    position: str = "WR",
    fantasy_positions: tuple[str, ...] = ("WR",),
    team: str | None = "KC",
    injury_status: str | None = None,
) -> Player:
    return Player(
        player_id=pid,
        full_name=full_name,
        position=position,
        fantasy_positions=fantasy_positions,
        team=team,
        status="Active",
        injury_status=injury_status,
    )


def make_snapshot(
    *,
    players: dict[str, Player],
    weekly_stats: dict[int, dict[str, dict[str, float]]],
    season: int,
    weeks_included: tuple[int, ...],
    snapshot_dir: str = "/fake/snapshot",
) -> SnapshotData:
    return SnapshotData(
        snapshot_dir=snapshot_dir,
        schema_version=1,
        season=season,
        weeks_included=weeks_included,
        upcoming_week_projection=weeks_included[-1] + 1 if weeks_included else None,
        players=players,
        weekly_stats=weekly_stats,
        prior_season_stats={},
    )


def league_routes(
    *,
    username: str = "cole",
    user_id: str = "U1",
    league_id: str = "L1",
    season: int = 2026,
    state_season: int = 2026,
    state_week: int = 5,
    user_roster_players: tuple[str, ...] = ("p1", "p2"),
    user_roster_starters: tuple[str, ...] | None = None,
    other_roster_players: tuple[str, ...] = (),
    roster_positions: tuple[str, ...] = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"),
    scoring_settings: dict[str, float] | None = None,
) -> dict[str, Any]:
    starters = (
        list(user_roster_starters) if user_roster_starters is not None else list(user_roster_players)
    )
    return {
        "/v1/state/nfl": {"season": state_season, "week": state_week},
        f"/v1/user/{username}": {
            "user_id": user_id,
            "username": username,
            "display_name": username.title(),
        },
        f"/v1/user/{user_id}/leagues/nfl/{season}": [
            {"league_id": league_id, "name": "Test League", "season": str(season)},
        ],
        f"/v1/league/{league_id}": {
            "league_id": league_id,
            "name": "Test League",
            "season": str(season),
            "roster_positions": list(roster_positions),
            "scoring_settings": scoring_settings or {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1},
        },
        f"/v1/league/{league_id}/rosters": [
            {
                "roster_id": 1,
                "owner_id": user_id,
                "players": list(user_roster_players),
                "starters": starters,
            },
            *(
                [
                    {
                        "roster_id": 2,
                        "owner_id": "U2",
                        "players": list(other_roster_players),
                        "starters": list(other_roster_players),
                    }
                ]
                if other_roster_players
                else []
            ),
        ],
    }


def fake_settings() -> Settings:
    return Settings(
        sleeper_base_url="https://api.sleeper.app",
        snapshot_backend="fs",
        snapshot_root=Path("/fake/snapshots"),
        gcs_bucket=None,
        gcs_prefix="seasons",
        cors_origins=["http://localhost:5173"],
        headshot_base_url="https://sleepercdn.com/content/nfl/players",
    )


def noop_prepare_season() -> PrepareSeason:
    def _prepare(season: int, live_state: object) -> None:
        return None

    return _prepare


@pytest.fixture
def make_client() -> Iterator[Any]:
    """Return a factory that builds a TestClient with fake deps wired."""

    app = create_app()

    def _factory(
        *,
        http: FakeHttp,
        snapshots: dict[int, SnapshotData],
    ) -> TestClient:
        app.dependency_overrides[get_settings] = fake_settings
        app.dependency_overrides[get_http_client] = lambda: http
        app.dependency_overrides[get_snapshot_reader] = lambda: FakeSnapshotReader(snapshots)
        app.dependency_overrides[get_prepare_season] = noop_prepare_season
        return TestClient(app)

    try:
        yield _factory
    finally:
        app.dependency_overrides.clear()
