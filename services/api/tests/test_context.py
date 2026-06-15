"""Regression tests for ``GET /leagues/{id}/context``.

The slot grid the React app renders comes from this endpoint. The
critical invariants:

- ``slot_id`` uses 1-based suffixes (RB1, RB2, BN1, BN2) and is unique.
- ``selectable`` is false for BN/IR/TAXI and true otherwise.
- ``starter_player`` aligns with ``starters`` index-by-index.
- ``bench`` is exactly the user's rostered players that are *not*
  starting.
"""

from __future__ import annotations

from tests.conftest import (
    FakeHttp,
    league_routes,
    make_player,
    make_snapshot,
)


def _setup() -> tuple[FakeHttp, dict[int, object]]:
    players = {
        "qb1": make_player("qb1", full_name="QB", position="QB",
                           fantasy_positions=("QB",), team="KC"),
        "rb1": make_player("rb1", full_name="RB", position="RB",
                           fantasy_positions=("RB",), team="PIT"),
        "wr1": make_player("wr1", full_name="WR", position="WR",
                           fantasy_positions=("WR",), team="LAR"),
        "te1": make_player("te1", full_name="TE", position="TE",
                           fantasy_positions=("TE",), team="NE"),
        "wr2": make_player("wr2", full_name="WR Two", position="WR",
                           fantasy_positions=("WR",), team="GB"),
        "rb_bench": make_player("rb_bench", full_name="Bench RB",
                                position="RB", fantasy_positions=("RB",), team="MIN"),
    }
    snap = make_snapshot(
        players=players,
        weekly_stats={1: {pid: {} for pid in players}},
        season=2026,
        weeks_included=(1,),
    )
    http = FakeHttp(
        league_routes(
            season=2026,
            state_season=2026,
            state_week=2,
            user_roster_players=("qb1", "rb1", "wr1", "te1", "wr2", "rb_bench"),
            user_roster_starters=("qb1", "rb1", "wr1", "te1", "wr2"),
            roster_positions=("QB", "RB", "WR", "TE", "FLEX", "BN"),
        )
    )
    return http, {2026: snap}


def test_slot_ids_are_unique_and_indexed(make_client) -> None:
    http, snaps = _setup()
    client = make_client(http=http, snapshots=snaps)
    res = client.get("/leagues/L1/context", params={"user": "cole", "season": 2026})
    assert res.status_code == 200, res.text
    body = res.json()
    slot_ids = [s["slot_id"] for s in body["slots"]]
    assert len(slot_ids) == len(set(slot_ids))
    assert slot_ids == ["QB1", "RB1", "WR1", "TE1", "FLEX1", "BN1"]


def test_bench_slots_are_marked_non_selectable(make_client) -> None:
    http, snaps = _setup()
    client = make_client(http=http, snapshots=snaps)
    body = client.get(
        "/leagues/L1/context", params={"user": "cole", "season": 2026}
    ).json()
    by_id = {s["slot_id"]: s for s in body["slots"]}
    assert by_id["BN1"]["selectable"] is False
    assert by_id["QB1"]["selectable"] is True
    assert by_id["FLEX1"]["selectable"] is True


def test_starter_player_index_aligns_with_roster_positions(make_client) -> None:
    """The starter list is index-aligned with ``roster_positions`` —
    the WR slot must point at the WR starter, not a random roster member."""

    http, snaps = _setup()
    client = make_client(http=http, snapshots=snaps)
    body = client.get(
        "/leagues/L1/context", params={"user": "cole", "season": 2026}
    ).json()
    by_id = {s["slot_id"]: s for s in body["slots"]}
    assert by_id["QB1"]["starter_player"]["player_id"] == "qb1"
    assert by_id["RB1"]["starter_player"]["player_id"] == "rb1"
    assert by_id["WR1"]["starter_player"]["player_id"] == "wr1"
    assert by_id["TE1"]["starter_player"]["player_id"] == "te1"
    assert by_id["FLEX1"]["starter_player"]["player_id"] == "wr2"


def test_bench_is_roster_minus_starters(make_client) -> None:
    http, snaps = _setup()
    client = make_client(http=http, snapshots=snaps)
    body = client.get(
        "/leagues/L1/context", params={"user": "cole", "season": 2026}
    ).json()
    bench_ids = {p["player_id"] for p in body["bench"]}
    assert bench_ids == {"rb_bench"}
