"""In-memory fakes for HttpClient + SnapshotReader used in unit tests."""

from __future__ import annotations

from typing import Any

from decision_engine.types import Player, SnapshotData


class FakeHttp:
    """HttpClient fake. ``routes`` maps path -> payload or exception."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        if path not in self._routes:
            raise AssertionError(f"FakeHttp: unexpected GET {path}")
        value = self._routes[path]
        if isinstance(value, BaseException):
            raise value
        return value


class FakeSnapshotReader:
    """SnapshotReader fake. Returns a prebuilt SnapshotData (or raises)."""

    def __init__(self, snapshot: SnapshotData | BaseException) -> None:
        self._snapshot = snapshot

    def load(self, season: int) -> SnapshotData:
        if isinstance(self._snapshot, BaseException):
            raise self._snapshot
        return self._snapshot


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
    players: dict[str, Player] | None = None,
    weekly_stats: dict[int, dict[str, dict[str, float]]] | None = None,
    prior_season_stats: dict[str, dict[str, float]] | None = None,
    season: int = 2026,
    weeks_included: tuple[int, ...] = (1, 2),
    snapshot_dir: str = "/fake/snapshots/2026-09-15",
) -> SnapshotData:
    return SnapshotData(
        snapshot_dir=snapshot_dir,
        schema_version=1,
        season=season,
        weeks_included=weeks_included,
        upcoming_week_projection=weeks_included[-1] + 1 if weeks_included else None,
        players=players or {},
        weekly_stats=weekly_stats or {},
        prior_season_stats=prior_season_stats or {},
    )


def league_routes(
    *,
    username: str = "cole",
    user_id: str = "U1",
    league_id: str = "L1",
    season: int = 2026,
    other_owner: str = "U2",
    user_roster_players: tuple[str, ...] = ("p1", "p2"),
    other_roster_players: tuple[str, ...] = ("p3", "p4"),
    scoring_settings: dict[str, float] | None = None,
    roster_positions: tuple[str, ...] = ("QB", "RB", "WR", "TE", "FLEX", "BN"),
) -> dict[str, Any]:
    """Default happy-path Sleeper routes for league-fetch tests."""

    return {
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
            "scoring_settings": scoring_settings or {"rec": 1.0, "rec_yd": 0.1},
        },
        f"/v1/league/{league_id}/rosters": [
            {
                "roster_id": 1,
                "owner_id": user_id,
                "players": list(user_roster_players),
                "starters": list(user_roster_players),
            },
            {
                "roster_id": 2,
                "owner_id": other_owner,
                "players": list(other_roster_players),
                "starters": list(other_roster_players),
            },
        ],
    }
