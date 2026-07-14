"""Tests for the GBT scoring model.

Synthetic snapshots small enough to reason about by hand, exercised
through the public build() -> score_player seam — same shape as the
context model's tests. Feature/regressor internals get direct unit
checks where hand-verifiable (ewma weights, trend windows, defense
index shrinkage, missing-value binning).
"""

from __future__ import annotations

import math

from decision_engine.core.scoring import gbt, naive
from decision_engine.types import Player, SnapshotData, WeeklyStats
from tests.unit.fakes import make_player, make_snapshot

SCORING = {"rec": 1.0, "rec_yd": 0.1}
SEASON = 2026


def _wr(pid: str, team: str = "KC") -> Player:
    return make_player(pid, position="WR", fantasy_positions=("WR",), team=team)


def _stat_line(targets: float, rec_yd: float) -> dict[str, float]:
    return {"rec": targets * 0.7, "rec_tgt": targets, "rec_yd": rec_yd}


def _training_snapshot(*, n_players: int = 30, n_weeks: int = 6) -> SnapshotData:
    """Many WRs whose weekly points scale with their target volume.

    30 players x 5 target weeks = 150 rows >= MIN_ROWS_PER_POSITION.
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


def test_trees_used_when_position_has_enough_rows() -> None:
    snap = _training_snapshot()
    score_fn = gbt.build(snap)
    score = score_fn(_wr("p3"), _history(snap, "p3"), SCORING, risk=0.5)

    assert any("WR boosted trees" in n for n in score.notes)
    # A sane fit lands near the player's own weekly points, not at 0.
    naive_score = naive.build(snap)(_wr("p3"), _history(snap, "p3"), SCORING, risk=0.5)
    assert math.isclose(score.projected_mean, naive_score.projected_mean, rel_tol=0.5)
    # sigma, confidence, and the risk formula are naive's, verbatim.
    assert math.isclose(score.projected_variance, naive_score.projected_variance)
    assert score.confidence == naive_score.confidence
    assert math.isclose(score.risk_adjusted_score, score.projected_mean)  # risk=0.5


def test_higher_volume_projects_higher() -> None:
    """p7 draws ~11 targets a week, p8 draws ~4 — the trees must rank
    the volume player above the dart throw."""

    snap = _training_snapshot()
    score_fn = gbt.build(snap)

    high = score_fn(_wr("p7"), _history(snap, "p7"), SCORING, risk=0.5)
    low = score_fn(_wr("p8"), _history(snap, "p8"), SCORING, risk=0.5)

    assert high.projected_mean > low.projected_mean


def test_thin_position_falls_back_to_naive_mean() -> None:
    """A position with < MIN_ROWS_PER_POSITION rows scores exactly like naive."""

    snap = _training_snapshot()  # WRs only: no QB rows at all
    qb = make_player("q1", position="QB", fantasy_positions=("QB",), team="KC")
    history = [WeeklyStats(season=SEASON, week=w, stats={"pass_yd": 250.0 + w}) for w in (1, 2, 3)]
    scoring = {"pass_yd": 0.04}

    gbt_score = gbt.build(snap)(qb, history, scoring, risk=0.7)
    naive_score = naive.build(snap)(qb, history, scoring, risk=0.7)

    assert any("naive fallback" in n for n in gbt_score.notes)
    assert math.isclose(gbt_score.projected_mean, naive_score.projected_mean)
    assert math.isclose(gbt_score.risk_adjusted_score, naive_score.risk_adjusted_score)


def test_uncovered_position_falls_back_to_naive() -> None:
    snap = _training_snapshot()
    kicker = make_player("k1", position="K", fantasy_positions=("K",), team="KC")
    history = [WeeklyStats(season=SEASON, week=w, stats={"fgm": 2.0}) for w in (1, 2, 3)]

    score = gbt.build(snap)(kicker, history, {"fgm": 3.0}, risk=0.5)

    assert any("not covered" in n for n in score.notes)
    naive_score = naive.build(snap)(kicker, history, {"fgm": 3.0}, risk=0.5)
    assert math.isclose(score.projected_mean, naive_score.projected_mean)


def test_zero_data_matches_naive_baseline() -> None:
    snap = _training_snapshot()
    rookie = _wr("rookie")

    score = gbt.build(snap)(rookie, [], SCORING, risk=0.5)

    assert score.projected_mean == 0.0
    assert score.projected_variance == naive.ZERO_DATA_VARIANCE
    assert score.confidence == "low"
    assert "no historical data" in score.notes


def test_prior_season_only_history_falls_back_to_naive() -> None:
    """Week-1 replay hands prior-season weeks; the trees must not fire."""

    snap = _training_snapshot()
    prior_history = [
        WeeklyStats(season=SEASON - 1, week=w, stats=_stat_line(6.0, 50.0)) for w in (1, 2, 3, 4)
    ]

    score = gbt.build(snap)(_wr("p9"), prior_history, SCORING, risk=0.5)
    naive_score = naive.build(snap)(_wr("p9"), prior_history, SCORING, risk=0.5)

    assert any("no current-season data" in n for n in score.notes)
    assert math.isclose(score.projected_mean, naive_score.projected_mean)


def test_fit_is_league_scoring_specific() -> None:
    snap = _training_snapshot()
    score_fn = gbt.build(snap)
    hist = _history(snap, "p5")

    ppr = score_fn(_wr("p5"), hist, {"rec": 1.0, "rec_yd": 0.1}, risk=0.5)
    standard = score_fn(_wr("p5"), hist, {"rec_yd": 0.1}, risk=0.5)

    assert ppr.projected_mean > standard.projected_mean


def test_fp_ewma_weights_recent_games() -> None:
    games = [gbt._Game(week=w, points=p, stats={}) for w, p in enumerate([10.0, 20.0], 1)]
    # weights 1 (older), 2 (newer): (1*10 + 2*20) / 3
    assert math.isclose(gbt._fp_ewma(games), 50.0 / 3.0)

    # one game is below the spec's minimum of 2 -> missing
    assert math.isnan(gbt._fp_ewma(games[:1]))


def test_fp_ewma_uses_last_five_games_only() -> None:
    points = [100.0, 1.0, 2.0, 3.0, 4.0, 5.0]  # the 100 falls out of the window
    games = [gbt._Game(week=w, points=p, stats={}) for w, p in enumerate(points, 1)]
    expected = (1 * 1 + 2 * 2 + 3 * 3 + 4 * 4 + 5 * 5) / 15.0
    assert math.isclose(gbt._fp_ewma(games), expected)


def test_trend_needs_five_values() -> None:
    assert math.isnan(gbt._trend_2v3([1.0, 2.0, 3.0, 4.0]))
    # mean(4, 5) - mean(1, 2, 3) = 4.5 - 2.0
    assert math.isclose(gbt._trend_2v3([1.0, 2.0, 3.0, 4.0, 5.0]), 2.5)


def test_fpa_index_shrinks_toward_neutral() -> None:
    tables = gbt._DefenseTables()
    # 4 games of allowing 10% more than expected: raw 1.1
    tables.fpa[("BUF", "WR")] = [110.0, 100.0, 4.0]
    # shrunk = (4 * 1.1 + 4 * 1.0) / 8 = 1.05
    assert math.isclose(tables.fpa_index("BUF", "WR"), 105.0)
    # no data yet -> neutral 100; unknown opponent -> missing
    assert tables.fpa_index("NYJ", "WR") == 100.0
    assert math.isnan(tables.fpa_index(None, "WR"))


def test_bin_value_reserves_bin_zero_for_missing() -> None:
    edges = (1.0, 2.0, 3.0)
    assert gbt._bin_value(edges, float("nan")) == 0
    assert gbt._bin_value(edges, 0.5) == 1
    assert gbt._bin_value(edges, 2.5) == 3
    assert gbt._bin_value(edges, 99.0) == 4


def test_home_flag_from_schedule() -> None:
    """With a schedule in the snapshot, the home flag reaches the
    feature vector (1 at home, 0 away, missing when unknown)."""

    games = [gbt._Game(week=w, points=10.0, stats=_stat_line(6.0, 50.0)) for w in (1, 2, 3)]
    home_teams = {4: frozenset({"KC"}), 5: frozenset({"BUF"})}

    def vector(week: int) -> tuple[float, ...]:
        return gbt._feature_vector(
            "WR",
            games,
            team="KC",
            week=week,
            opponent="BUF",
            team_weeks={},
            team_week_order={},
            home_teams=home_teams,
            defense=gbt._DefenseTables(),
            prior_fp_pg=float("nan"),
        )

    at_home = vector(4)
    away = vector(5)
    unknown = vector(6)

    home_i = 5  # shared block: ewma, std, trend, games, prior_fp, home, ...
    assert at_home[home_i] == 1.0
    assert away[home_i] == 0.0
    assert math.isnan(unknown[home_i])
    assert all(len(v) == gbt.N_FEATURES for v in (at_home, away, unknown))
