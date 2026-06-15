"""Tests for the atomic season-keyed snapshot writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stats_loader.clients.snapshot_writer import (
    BAK_PREFIX,
    TMP_PREFIX,
    AtomicSnapshotWriter,
    cleanup_stale_tmp,
)


def test_commit_writes_visible_folder_atomically(tmp_path: Path) -> None:
    writer = AtomicSnapshotWriter(tmp_path, 2025)
    writer.write_artifact("players.json", {"1000": {"player_id": "1000"}})

    # Before commit: nothing visible at the final path, but tmp folder exists.
    assert not (tmp_path / "2025").exists()
    tmp = writer.tmp_path
    assert tmp is not None and tmp.exists() and tmp.name.startswith(TMP_PREFIX)

    final = writer.commit({"schema_version": 1, "weeks_included": [1]})
    assert final == tmp_path / "2025"
    assert final.is_dir()
    assert (final / "manifest.json").is_file()
    assert (final / "players.json").is_file()
    # tmp folder is gone after rename.
    assert not tmp.exists()


def test_rerun_replaces_existing_season(tmp_path: Path) -> None:
    AtomicSnapshotWriter(tmp_path, 2025).commit({"schema_version": 1, "k": 1})
    final = AtomicSnapshotWriter(tmp_path, 2025).commit({"schema_version": 1, "k": 2})

    assert final == tmp_path / "2025"
    manifest = json.loads((final / "manifest.json").read_text())
    assert manifest["k"] == 2
    # No leftover .bak- folder after success.
    assert not any(p.name.startswith(BAK_PREFIX) for p in tmp_path.iterdir())


def test_different_seasons_coexist(tmp_path: Path) -> None:
    AtomicSnapshotWriter(tmp_path, 2024).commit({"schema_version": 1, "season": 2024})
    AtomicSnapshotWriter(tmp_path, 2025).commit({"schema_version": 1, "season": 2025})

    assert (tmp_path / "2024").is_dir()
    assert (tmp_path / "2025").is_dir()


def test_abort_after_partial_write_leaves_no_visible_snapshot(tmp_path: Path) -> None:
    writer = AtomicSnapshotWriter(tmp_path, 2025)
    writer.write_artifact("players.json", {"1000": {"player_id": "1000"}})
    writer.write_artifact("stats_week_1.json", {"1000": {"pass_yd": 100}})

    # Simulate a crash *before* commit. Abort removes the tmp folder; no
    # visible snapshot at the final path was ever created.
    writer.abort()
    assert not (tmp_path / "2025").exists()
    assert not any(p.name.startswith(TMP_PREFIX) for p in tmp_path.iterdir())


def test_cleanup_stale_tmp_removes_tmp_and_bak(tmp_path: Path) -> None:
    stale_tmp = tmp_path / f"{TMP_PREFIX}2025-99999"
    stale_tmp.mkdir(parents=True)
    (stale_tmp / "players.json").write_text("{}")
    stale_bak = tmp_path / f"{BAK_PREFIX}2024-99998"
    stale_bak.mkdir(parents=True)
    (stale_bak / "manifest.json").write_text("{}")
    # A legitimate season snapshot — must be untouched.
    real = tmp_path / "2024"
    real.mkdir()
    (real / "manifest.json").write_text("{}")

    removed = cleanup_stale_tmp(tmp_path)

    assert removed == 2
    assert not stale_tmp.exists()
    assert not stale_bak.exists()
    assert real.exists()


def test_manifest_written_last(tmp_path: Path) -> None:
    """write_artifact refuses to overwrite manifest.json; commit owns it."""

    writer = AtomicSnapshotWriter(tmp_path, 2025)
    with pytest.raises(ValueError, match="commit"):
        writer.write_artifact("manifest.json", {})


def test_artifacts_round_trip_through_json(tmp_path: Path) -> None:
    payload = {"player_id": "1000", "name": "José", "stats": {"rush_yd": 12.5}}
    writer = AtomicSnapshotWriter(tmp_path, 2025)
    writer.write_artifact("players.json", {"1000": payload})
    final = writer.commit({"schema_version": 1})

    written = json.loads((final / "players.json").read_text(encoding="utf-8"))
    assert written == {"1000": payload}
