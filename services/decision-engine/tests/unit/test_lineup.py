"""Tests for core.lineup.assign_lineup (PRD 3.3 optimal assignment)."""

from __future__ import annotations

import random

from decision_engine.core.lineup import assign_lineup
from decision_engine.types import Player, PlayerScore, ScoredCandidate
from tests.unit.fakes import make_player


def _candidate(
    pid: str,
    score: float,
    *,
    positions: tuple[str, ...] = ("WR",),
) -> ScoredCandidate:
    player = make_player(
        pid, position=positions[0], fantasy_positions=positions, team="KC"
    )
    return ScoredCandidate(
        player=player,
        score=PlayerScore(
            player_id=pid,
            projected_mean=score,
            projected_variance=1.0,
            risk_adjusted_score=score,
            confidence="high",
        ),
        final_score=score,
    )


def _eligible(player: Player, slot: str) -> bool:
    from decision_engine.core.eligibility import player_eligible_for_slot

    return player_eligible_for_slot(player, slot)


def test_superflex_before_qb_does_not_burn_the_qb() -> None:
    """Greedy in league order would put the QB (highest score) in the
    SUPER_FLEX and leave the QB slot empty. Optimal assignment starts
    both players."""

    candidates = {
        "qb": _candidate("qb", 22.0, positions=("QB",)),
        "wr": _candidate("wr", 15.0, positions=("WR",)),
    }
    out = assign_lineup(["SUPER_FLEX", "QB"], candidates, _eligible)
    assert out["QB1"] == "qb"
    assert out["SUPER_FLEX1"] == "wr"


def test_assignment_is_slot_order_invariant() -> None:
    """Mirror-order league produces the identical lineup."""

    candidates = {
        "qb": _candidate("qb", 22.0, positions=("QB",)),
        "wr": _candidate("wr", 15.0, positions=("WR",)),
        "rb": _candidate("rb", 12.0, positions=("RB",)),
    }
    fwd = assign_lineup(["SUPER_FLEX", "QB", "WR"], candidates, _eligible)
    rev = assign_lineup(["WR", "QB", "SUPER_FLEX"], candidates, _eligible)
    assert fwd["QB1"] == rev["QB1"] == "qb"
    assert fwd["WR1"] == rev["WR1"] == "wr"
    assert fwd["SUPER_FLEX1"] == rev["SUPER_FLEX1"] == "rb"


def test_best_player_lands_in_the_restrictive_slot_on_ties() -> None:
    """Interchangeable WRs: the highest-scored one fills WR1, not the
    FLEX — the intuitive arrangement the old greedy produced."""

    candidates = {
        "wr_best": _candidate("wr_best", 20.0),
        "wr_second": _candidate("wr_second", 15.0),
        "wr_third": _candidate("wr_third", 10.0),
    }
    out = assign_lineup(["WR", "WR", "FLEX"], candidates, _eligible)
    assert out["WR1"] == "wr_best"
    assert out["WR2"] == "wr_second"
    assert out["FLEX1"] == "wr_third"


def test_fullness_beats_total() -> None:
    """A weak-but-eligible player beats an empty slot: the RB with a
    negative score still starts rather than leaving RB1 empty."""

    candidates = {
        "wr": _candidate("wr", 20.0),
        "rb_bad": _candidate("rb_bad", -1.0, positions=("RB",)),
    }
    out = assign_lineup(["RB", "FLEX"], candidates, _eligible)
    assert out["RB1"] == "rb_bad"
    assert out["FLEX1"] == "wr"


def test_unfillable_slot_stays_empty() -> None:
    candidates = {"wr": _candidate("wr", 20.0)}
    out = assign_lineup(["QB", "WR"], candidates, _eligible)
    assert out["QB1"] is None
    assert out["WR1"] == "wr"


def test_deterministic_under_shuffled_insertion_order() -> None:
    rng = random.Random(7)
    pids = [f"wr{i}" for i in range(20)]
    scores = {pid: float(i % 7) + 0.25 * (i % 3) for i, pid in enumerate(pids)}

    baseline: dict[str, str | None] | None = None
    for _ in range(5):
        shuffled = list(pids)
        rng.shuffle(shuffled)
        candidates = {pid: _candidate(pid, scores[pid]) for pid in shuffled}
        out = assign_lineup(["WR", "WR", "FLEX"], candidates, _eligible)
        if baseline is None:
            baseline = out
        assert out == baseline


def test_oversized_lineups_fall_back_to_greedy() -> None:
    candidates = {
        "wr_best": _candidate("wr_best", 20.0),
        "wr_second": _candidate("wr_second", 15.0),
    }
    out = assign_lineup(["WR"] * 15, candidates, _eligible)
    assert out["WR1"] == "wr_best"
    assert out["WR2"] == "wr_second"
    assert out["WR3"] is None
