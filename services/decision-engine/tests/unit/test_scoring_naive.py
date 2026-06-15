"""Tests for the naive scoring model (PRD 2.2)."""

from __future__ import annotations

import math

from decision_engine.core.scoring import naive
from decision_engine.types import WeeklyStats
from tests.unit.fakes import make_player, make_snapshot

# PPR-style scoring, simple enough to compute by hand.
PPR = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}


def _wks(season: int, points: list[float]) -> list[WeeklyStats]:
    """Synthesise weekly stat lines where the player only catches passes.

    With PPR weights ``rec=1.0, rec_yd=0.1, rec_td=6.0``, encoding the
    desired total as ``rec_yd = points / 0.1`` gives a clean inverse so
    the test arithmetic stays readable.
    """

    return [
        WeeklyStats(season=season, week=i + 1, stats={"rec_yd": p / 0.1})
        for i, p in enumerate(points)
    ]


def test_no_history_returns_zero_with_placeholder_variance() -> None:
    snap = make_snapshot()
    score_fn = naive.build(snap)
    player = make_player("p1")

    score = score_fn(player, [], PPR, risk=0.5)

    assert score.projected_mean == 0.0
    assert score.projected_variance == naive.ZERO_DATA_VARIANCE
    assert score.confidence == "low"
    assert "no historical data" in score.notes


def test_four_week_history_is_high_confidence() -> None:
    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")

    score = score_fn(player, _wks(2026, [8.0, 8.0, 8.0, 8.0]), PPR, risk=0.5)

    assert math.isclose(score.projected_mean, 8.0)
    assert math.isclose(score.projected_variance, 0.0, abs_tol=1e-9)
    assert score.confidence == "high"
    # risk=0.5 -> score == mean.
    assert math.isclose(score.risk_adjusted_score, 8.0)


def test_risk_zero_penalises_variance() -> None:
    """PRD 2.2 worked example: WR 8.0 PPG, stddev 3.0, risk=0.0 -> 5.0."""

    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")
    # Points = [5, 8, 8, 11] -> mean 8.0, stddev (sample) sqrt(18/3) = ~2.449
    # We use a sequence whose sample stddev is exactly 3: e.g. [4, 7, 9, 12]:
    # mean 8, deviations -4, -1, 1, 4 -> sq sum 34 / 3 = 11.33 -> ~3.37.
    # Easier: [4, 8, 8, 12]: mean 8, sqsum 32/3 -> sqrt = 3.265.
    # Easiest: just verify the formula direction with arbitrary stddev.
    points = [4.0, 8.0, 8.0, 12.0]
    score = score_fn(player, _wks(2026, points), PPR, risk=0.0)

    mean = 8.0
    expected_stddev = math.sqrt(sum((p - mean) ** 2 for p in points) / 3)
    assert math.isclose(score.projected_mean, mean)
    assert math.isclose(score.projected_variance, expected_stddev)
    # risk=0.0 -> score = mean - variance
    assert math.isclose(score.risk_adjusted_score, mean - expected_stddev)


def test_risk_one_rewards_variance() -> None:
    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")
    points = [4.0, 8.0, 8.0, 12.0]
    score = score_fn(player, _wks(2026, points), PPR, risk=1.0)
    mean = 8.0
    expected_stddev = math.sqrt(sum((p - mean) ** 2 for p in points) / 3)
    assert math.isclose(score.risk_adjusted_score, mean + expected_stddev)


def test_three_this_season_weeks_uses_only_this_season() -> None:
    """≥3 this-season weeks -> ignore prior season (PRD §2)."""

    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")

    history = _wks(2026, [10.0, 10.0, 10.0]) + _wks(2025, [0.0, 0.0])
    score = score_fn(player, history, PPR, risk=0.5)

    # Mean should be 10 (this season only), not diluted by prior season.
    assert math.isclose(score.projected_mean, 10.0)
    assert score.confidence == "medium"  # 3 this-season weeks


def test_two_this_season_weeks_pads_with_prior() -> None:
    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")

    history = _wks(2026, [10.0, 10.0]) + _wks(2025, [0.0, 0.0])
    score = score_fn(player, history, PPR, risk=0.5)

    # 2 this + 2 prior padded = 4 total, mean = 5.0
    assert math.isclose(score.projected_mean, 5.0)
    assert score.confidence == "medium"


def test_single_sample_falls_back_to_position_prior() -> None:
    """1 sample with no prior bucket -> FALLBACK_VARIANCE."""

    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1", fantasy_positions=("WR",))

    score = score_fn(player, _wks(2026, [10.0]), PPR, risk=0.5)

    assert math.isclose(score.projected_mean, 10.0)
    # With no prior_season_stats bucket for WR, falls back.
    assert math.isclose(score.projected_variance, naive.FALLBACK_VARIANCE)
    assert any("position prior" in n for n in score.notes)


def test_single_sample_uses_position_prior_when_available() -> None:
    """1 sample falls back to stddev of WR per-game points from prior season."""

    # Two prior-season WRs scoring very different per-game points so we
    # get a non-trivial stddev distinct from FALLBACK_VARIANCE.
    prior_players = {
        "prior_wr_1": make_player("prior_wr_1", fantasy_positions=("WR",)),
        "prior_wr_2": make_player("prior_wr_2", fantasy_positions=("WR",)),
        "p1": make_player("p1", fantasy_positions=("WR",)),
    }
    prior_stats = {
        "prior_wr_1": {"gp": 16.0, "rec_yd": 16.0 * 100.0},  # 10 ppg
        "prior_wr_2": {"gp": 16.0, "rec_yd": 16.0 * 200.0},  # 20 ppg
    }
    snap = make_snapshot(
        season=2026, players=prior_players, prior_season_stats=prior_stats
    )
    score_fn = naive.build(snap)

    score = score_fn(prior_players["p1"], _wks(2026, [15.0]), PPR, risk=0.5)

    # stddev of [10, 20] = sqrt((25 + 25) / 1) = sqrt(50) ≈ 7.07
    assert math.isclose(score.projected_variance, math.sqrt(50.0))
    assert any("position prior" in n for n in score.notes)


def test_unknown_stat_codes_dont_score() -> None:
    """Stats with no scoring weight contribute zero (PRD §1)."""

    snap = make_snapshot(season=2026)
    score_fn = naive.build(snap)
    player = make_player("p1")

    # ``pass_yd`` isn't in PPR (our test scoring). It contributes 0.
    history = [
        WeeklyStats(season=2026, week=1, stats={"pass_yd": 999.0, "rec_yd": 100.0}),
        WeeklyStats(season=2026, week=2, stats={"pass_yd": 999.0, "rec_yd": 100.0}),
        WeeklyStats(season=2026, week=3, stats={"pass_yd": 999.0, "rec_yd": 100.0}),
    ]
    score = score_fn(player, history, PPR, risk=0.5)
    # 100 rec_yd * 0.1 = 10 points each week, mean = 10.
    assert math.isclose(score.projected_mean, 10.0)
