"""GcsSnapshotReader prefetch contract.

Regression for the milestone-3 dashboard bug: the reader prefetches a
fixed blob list and ``has_object`` answers only from that set, so any
artifact missing from the list is invisible to ``assemble_snapshot``.
``projections_week_<W>.json`` wasn't listed — the availability gate
never armed and blend silently degraded to context in every
GCS-served environment while filesystem-served runs worked.
"""

from __future__ import annotations

import json

from google.api_core.exceptions import NotFound

from api.snapshot_gcs import GcsSnapshotReader

MANIFEST = {
    "schema_version": 1,
    "season": 2025,
    "weeks_included": [1, 2],
    "upcoming_week_projection": 3,
    "prior_season_bootstrapped": False,
    "snapshot_finished_at": "2026-06-13T00:00:00+00:00",
}


class _FakeBlob:
    def __init__(self, store: dict[str, object], path: str) -> None:
        self._store = store
        self._path = path

    def download_as_bytes(self) -> bytes:
        if self._path not in self._store:
            raise NotFound(self._path)  # type: ignore[no-untyped-call]
        return json.dumps(self._store[self._path]).encode()


class _FakeBucket:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._store, path)


class _FakeClient:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._store)


def _reader(store: dict[str, object]) -> GcsSnapshotReader:
    return GcsSnapshotReader(
        "test-bucket",
        supported_schema_version=1,
        client=_FakeClient(store),
    )


def _store(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "seasons/2025/manifest.json": MANIFEST,
        "seasons/2025/players.json": {
            "p1": {"player_id": "p1", "full_name": "X", "fantasy_positions": ["WR"]},
        },
        "seasons/2025/stats_week_1.json": {"p1": {"rec_yd": 50.0}},
        "seasons/2025/stats_week_2.json": {"p1": {"rec_yd": 70.0}},
    }
    base.update(extra)
    return base


def test_projection_blobs_are_prefetched_and_loaded() -> None:
    snap = _reader(
        _store(
            **{
                "seasons/2025/projections_week_2.json": {"p1": {"gp": 1.0, "rec_yd": 60.0}},
                "seasons/2025/projections_week_3.json": {"p1": {"gp": 1.0, "rec_yd": 65.0}},
            }
        )
    ).load(2025)

    # Week 2 (completed) and week 3 (upcoming_week_projection) both land.
    assert set(snap.weekly_projections) == {2, 3}
    assert snap.weekly_projections[3]["p1"] == {"gp": 1.0, "rec_yd": 65.0}


def test_absent_projection_blobs_load_as_empty() -> None:
    snap = _reader(_store()).load(2025)
    assert snap.weekly_projections == {}
    assert set(snap.weekly_stats) == {1, 2}
