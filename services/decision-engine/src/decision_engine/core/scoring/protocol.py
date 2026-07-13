"""Public scoring contract — see PRD 2.2.

A scoring model is a *factory* taking the snapshot once at startup and
returning a ``ScoreFn`` with the documented 4-arg signature. The
factory layer lets a model precompute league-wide priors (e.g.
position-bucket stddev for the variance fallback) without polluting
the per-player call.
"""

from __future__ import annotations

from typing import Protocol

from decision_engine.types import (
    Player,
    PlayerScore,
    ScoringSettings,
    SnapshotData,
    WeeklyStats,
)


class ScoreFn(Protocol):
    """Per-player scoring. Documented signature in PRD 2.2."""

    def __call__(
        self,
        player: Player,
        stats_history: list[WeeklyStats],
        league_scoring: ScoringSettings,
        risk: float,
    ) -> PlayerScore: ...


class ScoreModelFactory(Protocol):
    """Builds a ``ScoreFn`` from the snapshot.

    Results are cached across requests (``scoring.build_score_fn``), so
    the returned closure must capture only *derived* values (priors,
    feature tables), never the ``SnapshotData`` itself — otherwise the
    cache pins whole snapshots in memory after the season cache has
    evicted them.
    """

    def __call__(self, snapshot: SnapshotData) -> ScoreFn: ...
