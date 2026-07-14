"""Contracts for ``core.replay`` — the shared week-replay used by the
API comparison router and the eval harness.

The load-bearing behaviours (mirroring the api-level comparison tests,
but exercised directly against the core function):

- Predictions are leakage-safe (model sees weeks < W); actuals come
  from week W, scored with the league's weights.
- The candidate pool and the "human" starters are the week-W matchup
  archive, not the live roster.
- The perfect-hindsight total respects slot eligibility and player
  uniqueness.
- A week with no recorded stats, or a roster with no matchup entry,
  raises ``UserInputError``.
"""

from __future__ import annotations

import pytest

from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.replay import (
    WeekComparison,
    perfect_lineup_total,
    replay_week_comparison,
)
from decision_engine.types import (
    League,
    LeagueContext,
    Matchup,
    Player,
    Roster,
    SleeperUser,
)
from tests.unit.fakes import FakeHttp, FakeSnapshotReader, make_player, make_snapshot

SEASON = 2026
SCORING = {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1}


def _players() -> dict[str, Player]:
    return {
        "wr_strong": make_player("wr_strong", position="WR", team="LAR"),
        "wr_weak": make_player("wr_weak", position="WR", team="GB"),
        "rb1": make_player(
            "rb1", position="RB", fantasy_positions=("RB",), team="PIT"
        ),
    }


def _weekly() -> dict[int, dict[str, dict[str, float]]]:
    # Weeks 1-2 train; week 3 is the target. Strong WR has the history
    # and the big week 3; the human started the weak WR.
    return {
        1: {"wr_strong": {"rec_yd": 200.0, "rec": 10.0},
            "wr_weak": {"rec_yd": 20.0, "rec": 1.0},
            "rb1": {"rush_yd": 80.0}},
        2: {"wr_strong": {"rec_yd": 220.0, "rec": 11.0},
            "wr_weak": {"rec_yd": 25.0, "rec": 2.0},
            "rb1": {"rush_yd": 90.0}},
        3: {"wr_strong": {"rec_yd": 150.0, "rec": 8.0},
            "wr_weak": {"rec_yd": 10.0, "rec": 1.0}},
    }


def _context(
    *,
    roster_positions: tuple[str, ...] = ("WR", "BN", "BN"),
    current_players: tuple[str, ...] = ("wr_strong", "wr_weak", "rb1"),
) -> LeagueContext:
    roster = Roster(
        roster_id=1,
        owner_id="U1",
        players=current_players,
        starters=("wr_weak",),
    )
    return LeagueContext(
        user=SleeperUser(user_id="U1"),
        league=League(
            league_id="L1",
            name="Test League",
            season=str(SEASON),
            roster_positions=roster_positions,
            scoring_settings=dict(SCORING),
        ),
        rosters=(roster,),
        user_roster=roster,
    )


def _replay(
    *,
    context: LeagueContext | None = None,
    matchups: list[Matchup] | None = None,
    week: int = 3,
    weekly_projections: dict[int, dict[str, dict[str, float]]] | None = None,
) -> WeekComparison:
    snapshot = make_snapshot(
        players=_players(),
        weekly_stats=_weekly(),
        weekly_projections=weekly_projections,
        season=SEASON,
        weeks_included=(1, 2, 3),
    )
    if matchups is None:
        matchups = [
            Matchup(
                roster_id=1,
                matchup_id=1,
                players=("wr_strong", "wr_weak", "rb1"),
                starters=("wr_weak",),
            )
        ]
    return replay_week_comparison(
        http=FakeHttp({}),
        snapshot_reader=FakeSnapshotReader(snapshot),
        snapshot=snapshot,
        league_context=context or _context(),
        matchups=matchups,
        season=SEASON,
        week=week,
        model="naive",
    )


def test_model_pick_vs_actual_starter_totals() -> None:
    result = _replay()

    wr = next(p for p in result.slot_picks if p.slot_id == "WR1")
    assert wr.model_player_id == "wr_strong"
    assert wr.human_player_id == "wr_weak"

    # Week-3 actuals under league scoring: strong 150*0.1+8 = 23.0,
    # weak 10*0.1+1 = 2.0. Prediction is the naive mean of weeks 1-2:
    # (30 + 33) / 2 = 31.5.
    assert result.model_actual == pytest.approx(23.0)
    assert result.human_actual == pytest.approx(2.0)
    assert result.model_predicted == pytest.approx(31.5)
    assert result.human_predicted == pytest.approx((3.0 + 4.5) / 2)
    assert result.perfect_actual == pytest.approx(23.0)
    assert result.predicted_mean["wr_strong"] == pytest.approx(31.5)


def test_pool_is_the_matchup_archive_not_the_current_roster() -> None:
    """The strong WR was traded away after week 3: present on the
    current roster, absent from the week-3 matchup. The model must not
    pick him, and the week-swapped context must show the archive roster."""

    result = _replay(
        matchups=[
            Matchup(
                roster_id=1,
                matchup_id=1,
                players=("wr_weak", "rb1"),
                starters=("wr_weak",),
            )
        ]
    )

    wr = next(p for p in result.slot_picks if p.slot_id == "WR1")
    assert wr.model_player_id == "wr_weak"
    assert set(result.league_context.user_roster.players) == {"wr_weak", "rb1"}


def test_availability_gate_blocks_model_but_not_human_scoring() -> None:
    """The strong WR has all the history but no week-3 projection entry
    (out per Sleeper's pre-kickoff view): the model must not pick him.
    The human who started the weak WR still gets his actual points —
    the gate applies to the model's pool, never to scoring reality."""

    result = _replay(
        matchups=[
            Matchup(
                roster_id=1,
                matchup_id=1,
                players=("wr_strong", "wr_weak", "rb1"),
                starters=("wr_strong",),
            )
        ],
        weekly_projections={
            3: {"wr_weak": {"gp": 1.0, "rec_yd": 20.0}},
        },
    )

    wr = next(p for p in result.slot_picks if p.slot_id == "WR1")
    assert wr.model_player_id == "wr_weak"
    assert wr.human_player_id == "wr_strong"
    # Human actual is the gated player's real week-3 line: 150*0.1+8.
    assert result.human_actual == pytest.approx(23.0)
    assert result.model_actual == pytest.approx(2.0)


def test_no_stats_for_week_raises() -> None:
    with pytest.raises(UserInputError, match="no recorded stats"):
        _replay(week=4)


def test_missing_matchup_raises() -> None:
    with pytest.raises(UserInputError, match="matchup"):
        _replay(
            matchups=[
                Matchup(roster_id=2, players=("rb1",), starters=("rb1",)),
            ]
        )


def test_perfect_lineup_respects_eligibility_and_uniqueness() -> None:
    """Two WR slots + FLEX: strong (23.0) + weak (2.0) fill the WR
    slots; rb1 has no week-3 row so FLEX stays empty. No player reuse."""

    snapshot = make_snapshot(
        players=_players(),
        weekly_stats=_weekly(),
        season=SEASON,
        weeks_included=(1, 2, 3),
    )
    total = perfect_lineup_total(
        ["WR", "WR", "FLEX"],
        ("wr_strong", "wr_weak", "rb1"),
        snapshot,
        snapshot.weekly_stats[3],
        SCORING,
    )
    assert total == pytest.approx(25.0)


def test_perfect_lineup_bails_on_oversized_lineups() -> None:
    snapshot = make_snapshot(
        players=_players(),
        weekly_stats=_weekly(),
        season=SEASON,
        weeks_included=(1, 2, 3),
    )
    assert (
        perfect_lineup_total(
            ["WR"] * 15,
            ("wr_strong",),
            snapshot,
            snapshot.weekly_stats[3],
            SCORING,
        )
        is None
    )
