"""Tests for the blend scoring model (PRD 3.2).

Synthetic snapshots in the style of test_scoring_context.py. The blend
is exercised through the public build() -> score_player seam. The
target week is ``max(weekly_projections)`` (the pipeline trims
projections to <= W), so fixtures attach a projection table for the
week after the last stats week.
"""

from __future__ import annotations

import math

from decision_engine.core.scoring import blend, context
from decision_engine.types import Player, SnapshotData, WeeklyStats
from tests.unit.fakes import make_player, make_snapshot

SCORING = {"rec": 1.0, "rec_yd": 0.1}
# A custom ruleset that scores receptions triple — proves the
# projection is scored under *league* rules, not Sleeper's pts_ppr.
CUSTOM_SCORING = {"rec": 3.0, "rec_yd": 0.1}
SEASON = 2026
TARGET_WEEK = 4


def _wr(pid: str, team: str = "KC") -> Player:
    return make_player(pid, position="WR", fantasy_positions=("WR",), team=team)


def _stat_line(targets: float, rec_yd: float) -> dict[str, float]:
    return {"rec": targets * 0.7, "rec_tgt": targets, "rec_yd": rec_yd}


def _snapshot(
    *,
    projections: dict[str, dict[str, float]] | None = None,
    n_weeks: int = 3,
) -> SnapshotData:
    players = {f"p{i}": _wr(f"p{i}") for i in range(6)}
    players["rookie"] = _wr("rookie")
    weekly: dict[int, dict[str, dict[str, float]]] = {}
    for w in range(1, n_weeks + 1):
        weekly[w] = {
            f"p{i}": _stat_line(4.0 + i, rec_yd=(4.0 + i) * 8.0) for i in range(6)
        }
    weekly_projections = (
        {TARGET_WEEK: projections} if projections is not None else None
    )
    return make_snapshot(
        players=players,
        weekly_stats=weekly,
        weekly_projections=weekly_projections,
        weeks_included=tuple(range(1, n_weeks + 1)),
    )


def _history(snapshot: SnapshotData, pid: str) -> list[WeeklyStats]:
    return [
        WeeklyStats(season=SEASON, week=w, stats=table[pid])
        for w, table in sorted(snapshot.weekly_stats.items())
        if pid in table
    ]


def test_no_projections_in_snapshot_degrades_to_context() -> None:
    snap = _snapshot(projections=None)
    b = blend.build(snap)(_wr("p1"), _history(snap, "p1"), SCORING, risk=0.3)
    c = context.build(snap)(_wr("p1"), _history(snap, "p1"), SCORING, risk=0.3)
    assert b == c


def test_missing_projection_entry_matches_context_with_note() -> None:
    snap = _snapshot(projections={"p2": {"gp": 1.0, "rec": 4.0, "rec_yd": 50.0}})
    b = blend.build(snap)(_wr("p1"), _history(snap, "p1"), SCORING, risk=0.5)
    c = context.build(snap)(_wr("p1"), _history(snap, "p1"), SCORING, risk=0.5)
    assert b.projected_mean == c.projected_mean
    assert b.risk_adjusted_score == c.risk_adjusted_score
    assert b.confidence == c.confidence
    assert "no weekly projection" in b.notes


def test_projection_is_the_mean_history_is_the_spread() -> None:
    """With a meaningful projection entry the mean is the projection
    itself (the 2024 tuning verdict — see blend.py docstring); the
    spread stays context's per-player stddev."""

    proj = {"p1": {"gp": 1.0, "rec": 2.0, "rec_yd": 20.0}}  # 4.0 pts under SCORING
    snap = _snapshot(projections=proj)
    hist = _history(snap, "p1")
    b = blend.build(snap)(_wr("p1"), hist, SCORING, risk=0.5)
    c = context.build(snap)(_wr("p1"), hist, SCORING, risk=0.5)

    assert math.isclose(b.projected_mean, 2.0 * 1.0 + 20.0 * 0.1)  # 4.0
    assert math.isclose(b.projected_variance, c.projected_variance)
    assert any("blend:" in n for n in b.notes)


def test_zero_history_uses_pure_projection() -> None:
    """A rookie with a projection is startable — not the 0.0/low
    dead-end context returns."""

    proj = {"rookie": {"gp": 1.0, "rec": 5.0, "rec_yd": 60.0}}
    snap = _snapshot(projections=proj)
    b = blend.build(snap)(_wr("rookie"), [], SCORING, risk=0.5)
    c = context.build(snap)(_wr("rookie"), [], SCORING, risk=0.5)

    assert c.projected_mean == 0.0
    assert b.projected_mean == 5.0 * 1.0 + 60.0 * 0.1  # pure projection
    assert b.confidence == "medium"  # bumped from context's low


def test_projection_scored_under_league_rules_not_pts_ppr() -> None:
    """The pts_ppr field must be ignored; only stat codes in the
    league's dict count (here rec is worth 3.0)."""

    proj = {
        "rookie": {"gp": 1.0, "rec": 5.0, "rec_yd": 60.0, "pts_ppr": 99.0},
    }
    snap = _snapshot(projections=proj)
    b = blend.build(snap)(_wr("rookie"), [], CUSTOM_SCORING, risk=0.5)
    assert b.projected_mean == 5.0 * 3.0 + 60.0 * 0.1  # 21.0, not 99


def test_gp_below_threshold_is_no_projection() -> None:
    """An ADP-noise-only entry (no real gp) is not a forecast."""

    proj = {"p1": {"gp": 0.0, "adp_dd_ppr": 999.0}}
    snap = _snapshot(projections=proj)
    b = blend.build(snap)(_wr("p1"), _history(snap, "p1"), SCORING, risk=0.5)
    assert "no weekly projection" in b.notes


def test_confidence_bumps_one_level_when_projection_exists() -> None:
    proj = {"p1": {"gp": 1.0, "rec": 2.0, "rec_yd": 20.0}}
    snap = _snapshot(projections=proj)
    hist = _history(snap, "p1")
    b = blend.build(snap)(_wr("p1"), hist, SCORING, risk=0.5)
    c = context.build(snap)(_wr("p1"), hist, SCORING, risk=0.5)
    order = ["low", "medium", "high"]
    assert order.index(b.confidence) == min(order.index(c.confidence) + 1, 2)


def test_deterministic() -> None:
    proj = {"p1": {"gp": 1.0, "rec": 2.0, "rec_yd": 20.0}}
    snap = _snapshot(projections=proj)
    hist = _history(snap, "p1")
    a = blend.build(snap)(_wr("p1"), hist, SCORING, risk=0.7)
    b = blend.build(snap)(_wr("p1"), hist, SCORING, risk=0.7)
    assert a == b
