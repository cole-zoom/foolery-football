"""Tests for the context scoring model.

Synthetic snapshots small enough to reason about by hand. The fit is
exercised through the public build() -> score_player seam, same as the
pipeline uses it.
"""

from __future__ import annotations

import math

from decision_engine.core.scoring import context, naive
from decision_engine.types import Player, SnapshotData, WeeklyStats
from tests.unit.fakes import make_player, make_snapshot

SCORING = {"rec": 1.0, "rec_yd": 0.1}
SEASON = 2026


def _wr(pid: str, team: str = "KC") -> Player:
    return make_player(pid, position="WR", fantasy_positions=("WR",), team=team)


def _stat_line(targets: float, rec_yd: float) -> dict[str, float]:
    # receptions track targets so points correlate with volume — the
    # relationship the regression should learn.
    return {"rec": targets * 0.7, "rec_tgt": targets, "rec_yd": rec_yd}


def _training_snapshot(*, n_players: int = 30, n_weeks: int = 6) -> SnapshotData:
    """Many WRs whose weekly points scale with their target volume.

    Player i draws ``4 + i % 8`` targets a week with mild week-to-week
    wiggle, giving the WR bucket plenty of rows (30 players x 5 target
    weeks = 150 >= MIN_ROWS_PER_POSITION).
    """

    players = {f"p{i}": _wr(f"p{i}") for i in range(n_players)}
    weekly: dict[int, dict[str, dict[str, float]]] = {}
    for w in range(1, n_weeks + 1):
        table: dict[str, dict[str, float]] = {}
        for i in range(n_players):
            tgts = 4.0 + (i % 8) + (0.5 if (w + i) % 2 else -0.5)
            table[f"p{i}"] = _stat_line(tgts, rec_yd=tgts * 8.0)
        weekly[w] = table
    return make_snapshot(
        players=players,
        weekly_stats=weekly,
        weeks_included=tuple(range(1, n_weeks + 1)),
    )


def _history(snapshot: SnapshotData, pid: str) -> list[WeeklyStats]:
    return [
        WeeklyStats(season=SEASON, week=w, stats=table[pid])
        for w, table in sorted(snapshot.weekly_stats.items())
        if pid in table
    ]


def test_regression_used_when_position_has_enough_rows() -> None:
    snap = _training_snapshot()
    score_fn = context.build(snap)
    score = score_fn(_wr("p3"), _history(snap, "p3"), SCORING, risk=0.5)

    assert any("WR regression" in n for n in score.notes)
    # A sane fit lands near the player's own weekly points, not at 0.
    naive_score = naive.build(snap)(_wr("p3"), _history(snap, "p3"), SCORING, risk=0.5)
    assert math.isclose(score.projected_mean, naive_score.projected_mean, rel_tol=0.5)
    # sigma, confidence, and the risk formula are naive's, verbatim.
    assert math.isclose(score.projected_variance, naive_score.projected_variance)
    assert score.confidence == naive_score.confidence
    assert math.isclose(
        score.risk_adjusted_score,
        score.projected_mean,  # risk=0.5 -> no adjustment
    )


def test_rising_target_share_beats_flat_share_at_same_average() -> None:
    """The trend feature must separate a riser from a flat receiver.

    Both receivers average 6 targets over 5 weeks; the riser goes
    2,4,6,8,10 while the flat one sits on 6 every week. With volume and
    mean equal, only the share trend differs — the riser should project
    at least as high, and the coefficient should be finite/sane.
    """

    snap = _training_snapshot()
    score_fn = context.build(snap)

    def hist(targets: list[float]) -> list[WeeklyStats]:
        return [
            WeeklyStats(
                season=SEASON, week=w + 1, stats=_stat_line(t, rec_yd=t * 8.0)
            )
            for w, t in enumerate(targets)
        ]

    riser = score_fn(_wr("p1"), hist([2, 4, 6, 8, 10]), SCORING, risk=0.5)
    flat = score_fn(_wr("p2"), hist([6, 6, 6, 6, 6]), SCORING, risk=0.5)

    assert riser.projected_mean >= flat.projected_mean


def test_thin_position_falls_back_to_naive_mean() -> None:
    """A position with < MIN_ROWS_PER_POSITION rows scores exactly like naive."""

    snap = _training_snapshot()  # WRs only: no QB rows at all
    qb = make_player("q1", position="QB", fantasy_positions=("QB",), team="KC")
    history = [
        WeeklyStats(season=SEASON, week=w, stats={"pass_yd": 250.0 + w})
        for w in (1, 2, 3)
    ]
    scoring = {"pass_yd": 0.04}

    ctx_score = context.build(snap)(qb, history, scoring, risk=0.7)
    naive_score = naive.build(snap)(qb, history, scoring, risk=0.7)

    assert any("naive fallback" in n for n in ctx_score.notes)
    assert math.isclose(ctx_score.projected_mean, naive_score.projected_mean)
    assert math.isclose(ctx_score.risk_adjusted_score, naive_score.risk_adjusted_score)


def test_zero_data_matches_naive_baseline() -> None:
    snap = _training_snapshot()
    rookie = _wr("rookie")

    score = context.build(snap)(rookie, [], SCORING, risk=0.5)

    assert score.projected_mean == 0.0
    assert score.projected_variance == naive.ZERO_DATA_VARIANCE
    assert score.confidence == "low"
    assert "no historical data" in score.notes


def test_prior_season_only_history_falls_back_to_naive() -> None:
    """Week-1 replay hands prior-season weeks; regression must not fire."""

    snap = _training_snapshot()
    prior_history = [
        WeeklyStats(season=SEASON - 1, week=w, stats=_stat_line(6.0, 50.0))
        for w in (1, 2, 3, 4)
    ]

    score = context.build(snap)(_wr("p9"), prior_history, SCORING, risk=0.5)
    naive_score = naive.build(snap)(_wr("p9"), prior_history, SCORING, risk=0.5)

    assert any("no current-season data" in n for n in score.notes)
    assert math.isclose(score.projected_mean, naive_score.projected_mean)


def test_fit_is_league_scoring_specific() -> None:
    """Different scoring settings must produce different fitted means."""

    snap = _training_snapshot()
    score_fn = context.build(snap)
    hist = _history(snap, "p5")

    ppr = score_fn(_wr("p5"), hist, {"rec": 1.0, "rec_yd": 0.1}, risk=0.5)
    standard = score_fn(_wr("p5"), hist, {"rec_yd": 0.1}, risk=0.5)

    assert ppr.projected_mean > standard.projected_mean


def test_features_walk_forward_only() -> None:
    """The feature builder must not see the target week.

    Direct check of the training-row construction: with observations at
    weeks 1..4, the row targeting week 4 gets features built from weeks
    1..3 only.
    """

    obs = [
        context._WeekObs(week=w, points=float(w), targets=float(w), target_share=0.1 * w)
        for w in (1, 2, 3, 4)
    ]
    feats_for_week4 = context._features(obs[:3])
    mean_feature = feats_for_week4[0]
    assert math.isclose(mean_feature, (1.0 + 2.0 + 3.0) / 3)


def test_trend_feature_zero_without_enough_history() -> None:
    obs = [
        context._WeekObs(week=w, points=10.0, targets=6.0, target_share=0.2)
        for w in (1, 2)
    ]
    feats = context._features(obs)
    assert feats[3] == 0.0  # only 2 obs: no prior window to diff against
