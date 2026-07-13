"""Tests for the scoring registry's ScoreFn build cache."""

from __future__ import annotations

import pytest

from decision_engine.core import scoring
from decision_engine.core.scoring import UnknownModelError, build_score_fn
from decision_engine.types import SnapshotData


def _snapshot(
    *,
    weeks: tuple[int, ...] = (1, 2),
    version: str | None = "2026-06-13T19:12:06-04:00",
) -> SnapshotData:
    return SnapshotData(
        snapshot_dir="/snapshots/2025",
        schema_version=1,
        season=2025,
        weeks_included=weeks,
        upcoming_week_projection=max(weeks) + 1 if weeks else None,
        players={},
        weekly_stats={w: {} for w in weeks},
        snapshot_version=version,
    )


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    scoring._build_cache.clear()


def test_same_trimmed_snapshot_reuses_score_fn() -> None:
    first = build_score_fn("naive", _snapshot())
    second = build_score_fn("naive", _snapshot())
    assert first is second


def test_different_week_trim_rebuilds() -> None:
    first = build_score_fn("naive", _snapshot(weeks=(1,)))
    second = build_score_fn("naive", _snapshot(weeks=(1, 2)))
    assert first is not second


def test_new_snapshot_version_rebuilds() -> None:
    first = build_score_fn("naive", _snapshot(version="v1"))
    second = build_score_fn("naive", _snapshot(version="v2"))
    assert first is not second


def test_versionless_snapshot_bypasses_cache() -> None:
    first = build_score_fn("naive", _snapshot(version=None))
    second = build_score_fn("naive", _snapshot(version=None))
    assert first is not second
    assert not scoring._build_cache


def test_cache_is_bounded() -> None:
    for w in range(1, scoring._BUILD_CACHE_MAX + 10):
        build_score_fn("naive", _snapshot(weeks=tuple(range(1, w + 1))))
    assert len(scoring._build_cache) == scoring._BUILD_CACHE_MAX


def test_unknown_model_still_raises() -> None:
    with pytest.raises(UnknownModelError, match="unknown scoring model"):
        build_score_fn("nope", _snapshot())
