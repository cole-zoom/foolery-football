"""Regression: ``/leagues/{id}/decisions`` must not recommend the same
player into multiple starter slots.

Before the fix the router scored each slot in isolation, so the best WR
landed in WR1, WR2 *and* FLEX simultaneously. The screenshot that
filed the bug showed Jaylen Warren in both RB slots and Puka Nacua in
WR1, WR2, and FLEX.
"""

from __future__ import annotations

from tests.conftest import (
    FakeHttp,
    league_routes,
    make_player,
    make_snapshot,
)


def _two_rb_two_wr_te_flex_snapshot() -> tuple[FakeHttp, dict[int, object]]:
    """League shape: QB, RB, RB, WR, WR, TE, FLEX, BN.

    Two RBs and two WRs of unequal strength so the bug is detectable —
    if duplicates leak through, the *best* RB and the *best* WR get
    repeated. With the fix each slot picks the next-best unused player.
    """

    players = {
        "qb1": make_player("qb1", full_name="QB One", position="QB",
                           fantasy_positions=("QB",), team="KC"),
        "rb_best": make_player("rb_best", full_name="Best RB", position="RB",
                               fantasy_positions=("RB",), team="PIT"),
        "rb_second": make_player("rb_second", full_name="Second RB", position="RB",
                                 fantasy_positions=("RB",), team="DET"),
        "wr_best": make_player("wr_best", full_name="Best WR", position="WR",
                               fantasy_positions=("WR",), team="LAR"),
        "wr_second": make_player("wr_second", full_name="Second WR", position="WR",
                                 fantasy_positions=("WR",), team="MIN"),
        "wr_third": make_player("wr_third", full_name="Third WR", position="WR",
                                fantasy_positions=("WR",), team="GB"),
        "te1": make_player("te1", full_name="TE One", position="TE",
                           fantasy_positions=("TE",), team="NE"),
    }
    # Higher numbers => stronger recent history => higher projection.
    weekly = {
        1: {
            "qb1": {"pass_yd": 300.0},
            "rb_best": {"rush_yd": 150.0},
            "rb_second": {"rush_yd": 60.0},
            "wr_best": {"rec_yd": 180.0, "rec": 10.0},
            "wr_second": {"rec_yd": 70.0, "rec": 5.0},
            "wr_third": {"rec_yd": 40.0, "rec": 3.0},
            "te1": {"rec_yd": 50.0, "rec": 4.0},
        },
        2: {
            "qb1": {"pass_yd": 310.0},
            "rb_best": {"rush_yd": 140.0},
            "rb_second": {"rush_yd": 70.0},
            "wr_best": {"rec_yd": 170.0, "rec": 9.0},
            "wr_second": {"rec_yd": 80.0, "rec": 6.0},
            "wr_third": {"rec_yd": 50.0, "rec": 4.0},
            "te1": {"rec_yd": 55.0, "rec": 4.0},
        },
    }
    season = 2026
    snap = make_snapshot(
        players=players,
        weekly_stats=weekly,
        season=season,
        weeks_included=(1, 2),
    )
    user_roster = tuple(players.keys())
    http = FakeHttp(
        league_routes(
            season=season,
            state_season=season,
            state_week=3,
            user_roster_players=user_roster,
            roster_positions=("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"),
        )
    )
    return http, {season: snap}


def test_decisions_does_not_repeat_a_player_across_slots(make_client) -> None:
    http, snapshots = _two_rb_two_wr_te_flex_snapshot()
    client = make_client(http=http, snapshots=snapshots)

    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    recommended_ids = [
        d["recommended"]["player"]["player_id"]
        for d in body["decisions"]
        if d["recommended"] is not None
    ]
    # No player_id may show up in more than one slot.
    assert len(recommended_ids) == len(set(recommended_ids)), (
        f"duplicate player recommended into multiple slots: {recommended_ids}"
    )


def test_decisions_fills_flex_with_next_best_unused(make_client) -> None:
    """The best WR fills WR1; the FLEX must fall back to the next unused
    RB/WR/TE rather than recycle the best WR."""

    http, snapshots = _two_rb_two_wr_te_flex_snapshot()
    client = make_client(http=http, snapshots=snapshots)

    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    by_slot_id = {d["slot_id"]: d for d in body["decisions"]}
    wr1 = by_slot_id["WR1"]["recommended"]
    wr2 = by_slot_id["WR2"]["recommended"]
    flex = by_slot_id["FLEX1"]["recommended"]
    rb1 = by_slot_id["RB1"]["recommended"]
    rb2 = by_slot_id["RB2"]["recommended"]

    assert wr1 is not None and wr2 is not None and flex is not None
    assert wr1["player"]["player_id"] == "wr_best"
    assert wr2["player"]["player_id"] == "wr_second"
    # Only RB/WR/TE leftover is the third WR — that's who FLEX should be.
    assert flex["player"]["player_id"] == "wr_third"

    assert rb1 is not None and rb2 is not None
    assert rb1["player"]["player_id"] == "rb_best"
    assert rb2["player"]["player_id"] == "rb_second"


def test_decisions_leaves_slot_empty_when_no_unique_pick(make_client) -> None:
    """If exclusion drains the eligible pool, the slot's ``recommended``
    field must be null rather than recycling a used player."""

    # Roster has one RB and one WR; FLEX has no remaining RB/WR/TE.
    players = {
        "rb": make_player("rb", full_name="The RB", position="RB",
                          fantasy_positions=("RB",), team="PIT"),
        "wr": make_player("wr", full_name="The WR", position="WR",
                          fantasy_positions=("WR",), team="LAR"),
        "qb": make_player("qb", full_name="The QB", position="QB",
                          fantasy_positions=("QB",), team="KC"),
        "te": make_player("te", full_name="The TE", position="TE",
                          fantasy_positions=("TE",), team="NE"),
    }
    weekly = {
        1: {
            "rb": {"rush_yd": 100.0},
            "wr": {"rec_yd": 100.0, "rec": 5.0},
            "qb": {"pass_yd": 300.0},
            "te": {"rec_yd": 60.0, "rec": 4.0},
        },
        2: {
            "rb": {"rush_yd": 110.0},
            "wr": {"rec_yd": 90.0, "rec": 6.0},
            "qb": {"pass_yd": 280.0},
            "te": {"rec_yd": 70.0, "rec": 5.0},
        },
    }
    season = 2026
    snap = make_snapshot(
        players=players,
        weekly_stats=weekly,
        season=season,
        weeks_included=(1, 2),
    )
    http = FakeHttp(
        league_routes(
            season=season,
            state_season=season,
            state_week=3,
            user_roster_players=tuple(players.keys()),
            roster_positions=("QB", "RB", "WR", "TE", "FLEX", "BN"),
        )
    )

    client = make_client(http=http, snapshots={season: snap})
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    by_slot_id = {d["slot_id"]: d for d in body["decisions"]}
    flex = by_slot_id["FLEX1"]
    assert flex["recommended"] is None
