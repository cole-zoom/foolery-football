"""Tests for clients.snapshot_reader (season-keyed layout)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decision_engine.clients.snapshot_reader import (
    FilesystemSnapshotReader,
    SnapshotMissingError,
    SnapshotSchemaError,
)


def _write_snapshot(
    root: Path,
    *,
    season: int,
    manifest: dict[str, object],
    players: dict[str, dict[str, object]],
    weekly: dict[int, dict[str, dict[str, float]]] | None = None,
    prior: dict[str, dict[str, float]] | None = None,
    schedule: list[object] | None = None,
) -> Path:
    snap_dir = root / str(season)
    snap_dir.mkdir(parents=True)
    (snap_dir / "manifest.json").write_text(json.dumps(manifest))
    (snap_dir / "players.json").write_text(json.dumps(players))
    if weekly:
        for w, table in weekly.items():
            (snap_dir / f"stats_week_{w}.json").write_text(json.dumps(table))
    if prior is not None:
        (snap_dir / "stats_prior_season.json").write_text(json.dumps(prior))
    if schedule is not None:
        (snap_dir / "schedule.json").write_text(json.dumps(schedule))
    return snap_dir


def _basic_manifest(
    *,
    season: int = 2025,
    weeks: list[int] | None = None,
    prior_bootstrapped: bool = False,
    schema_version: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "loader_version": "0.1.0",
        "snapshot_started_at": "2026-09-15T00:00:00+00:00",
        "snapshot_finished_at": "2026-09-15T00:00:42+00:00",
        "season": season,
        "completed_through_week": (weeks or [])[-1] if weeks else 0,
        "weeks_included": weeks or [],
        "upcoming_week_projection": ((weeks or [0])[-1] + 1) if weeks else None,
        "prior_season_bootstrapped": prior_bootstrapped,
        "sources": {},
    }


def test_load_reads_requested_season(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2024,
        manifest=_basic_manifest(season=2024, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "Old", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1, 2]),
        players={"p1": {"player_id": "p1", "full_name": "New", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}, 2: {"p1": {"rec_yd": 70.0}}},
    )

    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    snap = reader.load(2025)

    assert snap.snapshot_dir.endswith("2025")
    assert snap.season == 2025
    assert snap.players["p1"].full_name == "New"
    assert snap.weeks_included == (1, 2)
    assert 2 in snap.weekly_stats

    snap_2024 = reader.load(2024)
    assert snap_2024.season == 2024
    assert snap_2024.players["p1"].full_name == "Old"


def test_missing_season_raises_snapshot_missing(tmp_path: Path) -> None:
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    with pytest.raises(SnapshotMissingError, match="2025"):
        reader.load(2025)


def test_missing_root_raises_snapshot_missing(tmp_path: Path) -> None:
    reader = FilesystemSnapshotReader(tmp_path / "absent", supported_schema_version=1)
    with pytest.raises(SnapshotMissingError):
        reader.load(2025)


def test_newer_schema_rejected(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1], schema_version=99),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    with pytest.raises(SnapshotSchemaError, match="schema_version"):
        reader.load(2025)


def test_manifest_season_mismatch_raises(tmp_path: Path) -> None:
    """Folder name says 2025 but manifest says 2024 — flag the corruption."""

    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2024, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    with pytest.raises(SnapshotSchemaError, match="declares season 2024"):
        reader.load(2025)


def test_missing_weekly_stats_file_raises(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1, 2]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
        # week 2 file deliberately absent
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    with pytest.raises(SnapshotSchemaError, match="stats_week_2"):
        reader.load(2025)


def test_schedule_absent_yields_empty_mapping(tmp_path: Path) -> None:
    """Old snapshots (no schedule.json) must keep loading fine."""

    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    snap = reader.load(2025)
    assert snap.schedule == {}


def test_schedule_parsed_into_week_team_opponent(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
        schedule=[
            {"week": 1, "home": "KC", "away": "BUF", "status": "complete"},
            {"week": 2, "home": "BUF", "away": "NYJ", "status": "pre_game"},
            {"week": 2, "home": "not-a-game"},  # malformed: skipped, not fatal
        ],
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    snap = reader.load(2025)

    # Both directions of each game are addressable.
    assert snap.schedule[1] == {"KC": "BUF", "BUF": "KC"}
    assert snap.schedule[2] == {"BUF": "NYJ", "NYJ": "BUF"}
    # Home sides survive (the symmetric opponent map loses them).
    assert snap.home_teams[1] == frozenset({"KC"})
    assert snap.home_teams[2] == frozenset({"BUF"})


def test_schedule_wrong_top_level_type_raises(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    (tmp_path / "2025" / "schedule.json").write_text(json.dumps({"week": 1}))
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    with pytest.raises(SnapshotSchemaError, match="expected array"):
        reader.load(2025)


def test_snapshot_version_lifted_from_manifest(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[1]),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        weekly={1: {"p1": {"rec_yd": 50.0}}},
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    snap = reader.load(2025)
    assert snap.snapshot_version == "2026-09-15T00:00:42+00:00"


def test_loads_prior_season_when_bootstrapped(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        season=2025,
        manifest=_basic_manifest(season=2025, weeks=[], prior_bootstrapped=True),
        players={"p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]}},
        prior={"p1": {"gp": 16.0, "rec_yd": 1200.0}},
    )
    reader = FilesystemSnapshotReader(tmp_path, supported_schema_version=1)
    snap = reader.load(2025)
    assert snap.prior_season_stats == {"p1": {"gp": 16.0, "rec_yd": 1200.0}}
