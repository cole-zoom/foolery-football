"""Tests for the scratch scoring model (milestone 4 PRD).

Synthetic snapshots in the style of test_scoring_context.py. The
defining property under test: scratch is *sleeperless* — its output
must be identical whether or not the snapshot carries Sleeper
projections.
"""

from __future__ import annotations

import math

from decision_engine.core.scoring import naive, scratch
from decision_engine.core.scoring.scratch import _DefenseIndex
from decision_engine.types import Player, SnapshotData, WeeklyStats
from tests.unit.fakes import make_player, make_snapshot

SCORING = {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "pass_yd": 0.04, "fgm": 3.0}
SEASON = 2026
N_WEEKS = 6
TARGET_WEEK = N_WEEKS + 1

TEAMS = ["T00", "T01", "T02", "T03", "T04", "T05"]


def _wr(pid: str, team: str = "T00") -> Player:
    return make_player(pid, position="WR", fantasy_positions=("WR",), team=team)


def _stat_line(targets: float, rec_yd: float) -> dict[str, float]:
    return {"rec": targets * 0.7, "rec_tgt": targets, "rec_yd": rec_yd}


def _training_snapshot() -> SnapshotData:
    """30 WRs, 6 teams, round-robin-ish schedule, points scale with volume."""

    players = {f"p{i}": _wr(f"p{i}", team=TEAMS[i % len(TEAMS)]) for i in range(30)}
    weekly: dict[int, dict[str, dict[str, float]]] = {}
    schedule: dict[int, dict[str, str]] = {}
    home: dict[int, frozenset[str]] = {}
    for w in range(1, TARGET_WEEK + 1):
        pairs = [(TEAMS[k], TEAMS[(k + w) % len(TEAMS)]) for k in range(0, len(TEAMS), 2)]
        games: dict[str, str] = {}
        homes: set[str] = set()
        for a, b in pairs:
            if a == b:
                b = TEAMS[(TEAMS.index(a) + 1) % len(TEAMS)]
            games[a] = b
            games[b] = a
            homes.add(a)
        schedule[w] = games
        home[w] = frozenset(homes)
        if w <= N_WEEKS:
            table: dict[str, dict[str, float]] = {}
            for i in range(30):
                tgts = 4.0 + (i % 8) + (0.5 if (w + i) % 2 else -0.5)
                table[f"p{i}"] = _stat_line(tgts, rec_yd=tgts * 8.0)
            weekly[w] = table
    return make_snapshot(
        players=players,
        weekly_stats=weekly,
        weeks_included=tuple(range(1, N_WEEKS + 1)),
        schedule=schedule,
        home_teams=home,
    )


def _history(snapshot: SnapshotData, pid: str) -> list[WeeklyStats]:
    return [
        WeeklyStats(season=SEASON, week=w, stats=table[pid])
        for w, table in sorted(snapshot.weekly_stats.items())
        if pid in table
    ]


def test_regression_used_and_sane() -> None:
    snap = _training_snapshot()
    score_fn = scratch.build(snap)
    score = score_fn(_wr("p3", TEAMS[3]), _history(snap, "p3"), SCORING, risk=0.5)

    assert any("WR regression" in n for n in score.notes)
    naive_score = naive.build(snap)(_wr("p3", TEAMS[3]), _history(snap, "p3"), SCORING, 0.5)
    # A sane fit lands near the player's own level, not at zero.
    assert math.isclose(score.projected_mean, naive_score.projected_mean, rel_tol=0.5)
    assert math.isclose(score.projected_variance, naive_score.projected_variance)


def test_output_is_identical_with_and_without_projections() -> None:
    """The sleeperless contract: weekly_projections must never leak in."""

    snap = _training_snapshot()
    with_proj = snap.model_copy(
        update={
            "weekly_projections": {
                TARGET_WEEK: {"p3": {"gp": 1.0, "rec": 99.0, "rec_yd": 999.0}}
            }
        }
    )
    a = scratch.build(snap)(_wr("p3", TEAMS[3]), _history(snap, "p3"), SCORING, 0.5)
    b = scratch.build(with_proj)(_wr("p3", TEAMS[3]), _history(with_proj, "p3"), SCORING, 0.5)
    assert a == b


def test_defense_index_ratio_orders_generous_above_stingy() -> None:
    """AAA has allowed 30 pts/game to WRs, BBB 10 -> AAA ratio > 1 > BBB,
    both shrunk toward the 20-point league mean."""

    allowed = {
        ("AAA", "WR"): {1: {"rec_yd": 300.0}, 2: {"rec_yd": 300.0}},
        ("BBB", "WR"): {1: {"rec_yd": 100.0}, 2: {"rec_yd": 100.0}},
    }
    idx = _DefenseIndex(allowed, {"rec_yd": 0.1})
    aaa = idx.ratio("AAA", "WR", 3)
    bbb = idx.ratio("BBB", "WR", 3)
    assert aaa > 1.0 > bbb
    # Shrinkage: 2 games of signal vs 4 pseudo-games -> ratio well inside
    # the raw 1.5x / 0.5x extremes.
    assert aaa < 1.5
    assert bbb > 0.5
    # Unknown team / no data -> neutral.
    assert idx.ratio("CCC", "WR", 3) == 1.0
    assert idx.ratio("AAA", "WR", 1) == 1.0


def test_kicker_mean_scaled_by_opponent() -> None:
    """K path: recency mean x clamped opponent ratio. Same kicker history,
    generous vs stingy target-week opponent -> different means."""

    players = {
        "k1": make_player("k1", position="K", fantasy_positions=("K",), team="T00"),
        "k2": make_player("k2", position="K", fantasy_positions=("K",), team="T02"),
        "k3": make_player("k3", position="K", fantasy_positions=("K",), team="T04"),
    }
    # T01's defense faces k1 in the target week and has allowed a lot to
    # kickers; T03's has allowed little (k2's target opponent). k3
    # produced those numbers *against* them in weeks 1-2.
    weekly = {
        1: {"k1": {"fgm": 2.0}, "k2": {"fgm": 2.0}, "k3": {"fgm": 4.0}},
        2: {"k1": {"fgm": 2.0}, "k2": {"fgm": 2.0}, "k3": {"fgm": 0.5}},
    }
    schedule = {
        1: {"T04": "T01", "T01": "T04", "T00": "T05", "T05": "T00", "T02": "T06", "T06": "T02"},
        2: {"T04": "T03", "T03": "T04", "T00": "T05", "T05": "T00", "T02": "T06", "T06": "T02"},
        3: {"T00": "T01", "T01": "T00", "T02": "T03", "T03": "T02"},
    }
    snap = make_snapshot(
        players=players,
        weekly_stats=weekly,
        weeks_included=(1, 2),
        schedule=schedule,
    )
    score_fn = scratch.build(snap)
    vs_generous = score_fn(players["k1"], _history(snap, "k1"), SCORING, 0.5)
    vs_stingy = score_fn(players["k2"], _history(snap, "k2"), SCORING, 0.5)
    assert vs_generous.projected_mean > vs_stingy.projected_mean
    assert any("opp x" in n for n in vs_generous.notes)


def test_zero_history_stays_low_confidence_zero() -> None:
    snap = _training_snapshot()
    score = scratch.build(snap)(_wr("rookie", "T00"), [], SCORING, risk=0.5)
    assert score.projected_mean == 0.0
    assert score.confidence == "low"


def test_deterministic() -> None:
    snap = _training_snapshot()
    hist = _history(snap, "p1")
    a = scratch.build(snap)(_wr("p1", TEAMS[1]), hist, SCORING, 0.7)
    b = scratch.build(snap)(_wr("p1", TEAMS[1]), hist, SCORING, 0.7)
    assert a == b
