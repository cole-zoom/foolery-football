"""End-to-end integration test against the real Sleeper API.

Skipped by default. Run with:

    STATS_LOADER_INTEGRATION=1 uv run pytest -m integration

Hits ``/v1/state/nfl`` for the live current state, then snapshots a
single past-season week (using --season/--week overrides) so the test
is deterministic against historical data and doesn't depend on what
week of the season it is when you read this.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stats_loader.clients.http import SleeperHttpClient
from stats_loader.clients.snapshot_writer import AtomicSnapshotWriter
from stats_loader.core import pipeline
from stats_loader.types import NflState

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.environ.get("STATS_LOADER_INTEGRATION") == "1"


@pytest.mark.skipif(not _enabled(), reason="set STATS_LOADER_INTEGRATION=1 to run")
def test_state_endpoint_returns_current_season_week() -> None:
    with SleeperHttpClient("https://api.sleeper.app") as http:
        payload = http.get_json("/v1/state/nfl")
    assert isinstance(payload, dict)
    state = NflState.model_validate(payload)
    # No exact assertion on values — Sleeper's state is live. But the
    # season must be plausibly NFL-sized and the week a real integer.
    assert 2020 <= state.season <= 2100
    assert 0 <= state.week <= 22


@pytest.mark.skipif(not _enabled(), reason="set STATS_LOADER_INTEGRATION=1 to run")
def test_past_season_week_writes_real_snapshot(tmp_path: Path) -> None:
    """Snapshot a single past-season week and check the on-disk shape.

    Uses season=2024 week=2 — far enough in the past that the data is
    stable. (See docs/references/sleeper-api.md.)
    """

    season, week = 2024, 2

    with SleeperHttpClient("https://api.sleeper.app") as http:
        result = pipeline.run(
            http=http,
            writer_factory=lambda s: AtomicSnapshotWriter(tmp_path, s),
            state_override=NflState(season=season, week=week),
            now=datetime.now(UTC),
            dry_run=False,
        )

    assert result.snapshot_path is not None
    snapshot = result.snapshot_path
    assert (snapshot / "manifest.json").is_file()
    assert (snapshot / "players.json").is_file()
    assert (snapshot / f"stats_week_{week - 1}.json").is_file()
    assert (snapshot / f"projections_week_{week - 1}.json").is_file()
    # Upcoming-week projection (== week itself) also written.
    assert (snapshot / f"projections_week_{week}.json").is_file()

    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["season"] == season
    assert manifest["completed_through_week"] == week - 1
    assert manifest["schema_version"] == 1

    # Player payload smoke check.
    players = json.loads((snapshot / "players.json").read_text())
    assert isinstance(players, dict)
    assert len(players) > 1000
