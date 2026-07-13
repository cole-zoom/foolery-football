"""In-memory fakes for HttpClient / SnapshotWriter used in unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeHttp:
    """HttpClient fake. ``routes`` maps path -> payload (or exception)."""

    def __init__(self, routes: dict[str, Any], base_url: str = "https://api.fake") -> None:
        self._routes = routes
        self._base_url = base_url
        self.calls: list[str] = []

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        if path not in self._routes:
            raise AssertionError(f"FakeHttp: unexpected GET {path}")
        value = self._routes[path]
        if isinstance(value, BaseException):
            raise value
        return value


class FakeWriter:
    """SnapshotWriter fake. Records artifacts in memory; commit returns a stub path."""

    def __init__(self) -> None:
        self.artifacts: dict[str, object] = {}
        self.committed_manifest: dict[str, object] | None = None
        self.commit_path = Path("/fake/snapshots/2026-09-15")

    def write_artifact(self, name: str, payload: object) -> None:
        if name in self.artifacts:
            raise AssertionError(f"FakeWriter: duplicate write of {name}")
        self.artifacts[name] = payload

    def commit(self, manifest_payload: dict[str, object]) -> Path:
        self.committed_manifest = manifest_payload
        return self.commit_path


def make_players(count: int = 1500) -> dict[str, dict[str, object]]:
    """Build a plausible /v1/players/nfl payload with the load-bearing fields."""

    positions = ["QB", "RB", "WR", "TE", "K", "DEF"]
    out: dict[str, dict[str, object]] = {}
    for i in range(count):
        pid = str(1000 + i)
        out[pid] = {
            "player_id": pid,
            "full_name": f"Player {i}",
            "position": positions[i % len(positions)],
            "fantasy_positions": [positions[i % len(positions)]],
            "team": "KC",
            "status": "Active",
            "injury_status": None,
        }
    return out


def make_weekly(player_ids: list[str]) -> dict[str, dict[str, float]]:
    """Build a plausible weekly stats/projections payload."""

    return {pid: {"pass_yd": 200.0, "rush_td": 1.0} for pid in player_ids}


def make_schedule(weeks: int = 18) -> list[dict[str, object]]:
    """Build a plausible /schedule/nfl/regular/<season> payload."""

    return [
        {
            "status": "complete",
            "date": "2026-09-13",
            "week": w,
            "home": "KC",
            "away": "BUF",
            "game_id": f"20261{w:02d}02",
        }
        for w in range(1, weeks + 1)
    ]
