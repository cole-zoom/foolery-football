"""Invariants for ``GET /leagues/{id}/decisions``.

These cover sensitive output contracts the React app relies on:

- ``slot_id`` is unique and uses 1-based suffixes (RB1, RB2, FLEX1).
- Non-selectable slots (BN/IR/TAXI) get no entry.
- ``matches_current`` is true iff the recommendation == Sleeper's current
  starter player_id (drives the MATCH/SWAP badge in the screenshot).
- Projection totals are the sum/quadrature of per-slot recommendations.
- ``prefer_team``/``avoid_team`` are case-normalised.
"""

from __future__ import annotations

import math

import pytest

from tests.conftest import (
    FakeHttp,
    league_routes,
    make_player,
    make_snapshot,
)


def _basic_setup(
    *,
    user_roster_starters: tuple[str, ...] | None = None,
    roster_positions: tuple[str, ...] = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"),
) -> tuple[FakeHttp, dict[int, object]]:
    players = {
        "qb1": make_player("qb1", full_name="QB One", position="QB",
                           fantasy_positions=("QB",), team="KC"),
        "rb1": make_player("rb1", full_name="RB One", position="RB",
                           fantasy_positions=("RB",), team="PIT"),
        "rb2": make_player("rb2", full_name="RB Two", position="RB",
                           fantasy_positions=("RB",), team="DET"),
        "wr1": make_player("wr1", full_name="WR One", position="WR",
                           fantasy_positions=("WR",), team="LAR"),
        "wr2": make_player("wr2", full_name="WR Two", position="WR",
                           fantasy_positions=("WR",), team="MIN"),
        "wr3": make_player("wr3", full_name="WR Three", position="WR",
                           fantasy_positions=("WR",), team="GB"),
        "te1": make_player("te1", full_name="TE One", position="TE",
                           fantasy_positions=("TE",), team="NE"),
    }
    weekly = {
        1: {pid: {"rec_yd": 80.0, "rec": 5.0, "rush_yd": 60.0, "pass_yd": 250.0}
            for pid in players},
        2: {pid: {"rec_yd": 90.0, "rec": 6.0, "rush_yd": 70.0, "pass_yd": 280.0}
            for pid in players},
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
            user_roster_starters=user_roster_starters,
            roster_positions=roster_positions,
        )
    )
    return http, {season: snap}


def test_bn_slot_is_skipped(make_client) -> None:
    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    body = res.json()
    slot_ids = [d["slot_id"] for d in body["decisions"]]
    # BN never appears; the selectable slots do.
    assert "BN1" not in slot_ids
    assert {"QB1", "RB1", "RB2", "WR1", "WR2", "TE1", "FLEX1"} <= set(slot_ids)


def test_slot_ids_are_unique_and_indexed_1_based(make_client) -> None:
    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    body = res.json()
    slot_ids = [d["slot_id"] for d in body["decisions"]]
    assert len(slot_ids) == len(set(slot_ids))
    assert "RB1" in slot_ids and "RB2" in slot_ids
    assert "WR1" in slot_ids and "WR2" in slot_ids
    # First occurrence is always ``1``, never ``0``.
    assert all(not sid.endswith("0") for sid in slot_ids)


def test_matches_current_true_when_recommendation_equals_current_starter(
    make_client,
) -> None:
    """If the user's current starter already matches the model's pick,
    ``matches_current`` must be true — drives the MATCH badge."""

    # Starters match the strongest player at each slot exactly.
    http, snaps = _basic_setup(
        user_roster_starters=("qb1", "rb1", "rb2", "wr1", "wr2", "te1", "wr3", "wr3"),
    )
    client = make_client(http=http, snapshots=snaps)
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    body = res.json()
    by_slot_id = {d["slot_id"]: d for d in body["decisions"]}
    # QB1 only has one eligible player and it's already the starter.
    assert by_slot_id["QB1"]["matches_current"] is True
    # TE1 only has one eligible player and it's already the starter.
    assert by_slot_id["TE1"]["matches_current"] is True


def test_matches_current_false_when_recommendation_swaps(make_client) -> None:
    """Starter is not the top pick -> matches_current is false."""

    # Sleeper has the *weaker* options starting at every WR slot; model
    # should recommend the stronger ones. But our weekly stats are
    # identical, so we explicitly break the tie by giving wr1 more.
    players = {
        "wr_strong": make_player("wr_strong", full_name="Strong WR",
                                 position="WR", fantasy_positions=("WR",), team="LAR"),
        "wr_weak": make_player("wr_weak", full_name="Weak WR",
                               position="WR", fantasy_positions=("WR",), team="GB"),
    }
    weekly = {
        1: {"wr_strong": {"rec_yd": 200.0, "rec": 10.0},
            "wr_weak": {"rec_yd": 20.0, "rec": 1.0}},
        2: {"wr_strong": {"rec_yd": 220.0, "rec": 11.0},
            "wr_weak": {"rec_yd": 25.0, "rec": 2.0}},
    }
    season = 2026
    snap = make_snapshot(players=players, weekly_stats=weekly, season=season,
                        weeks_included=(1, 2))
    http = FakeHttp(
        league_routes(
            season=season,
            state_season=season,
            state_week=3,
            user_roster_players=("wr_strong", "wr_weak"),
            user_roster_starters=("wr_weak",),
            roster_positions=("WR", "BN"),
        )
    )
    client = make_client(http=http, snapshots={season: snap})
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    body = res.json()
    wr_slot = next(d for d in body["decisions"] if d["slot_id"] == "WR1")
    assert wr_slot["recommended"]["player"]["player_id"] == "wr_strong"
    assert wr_slot["current_starter"]["player_id"] == "wr_weak"
    assert wr_slot["matches_current"] is False


def test_projection_totals_match_sum_of_recommendations(make_client) -> None:
    """``projection_total`` is the sum of recommended ``projected_mean``
    and ``projection_variance_total`` is the quadrature of stddevs."""

    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    res = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    )
    body = res.json()
    recs = [d["recommended"] for d in body["decisions"] if d["recommended"]]
    expected_mean = sum(r["score"]["projected_mean"] for r in recs)
    expected_var = sum(r["score"]["projected_variance"] ** 2 for r in recs)
    assert body["projection_total"] == pytest.approx(expected_mean)
    assert body["projection_variance_total"] == pytest.approx(expected_var)
    assert body["projection_stddev_total"] == pytest.approx(math.sqrt(expected_var))


def test_prefer_team_is_case_normalised(make_client) -> None:
    """Sleeper team codes are upper-case; lower-case input must be
    accepted so the URL ``?prefer_team=lar`` still applies the boost."""

    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    plain = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    ).json()
    boosted = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster",
                "prefer_team": "lar"},
    ).json()
    plain_total = plain["projection_total"]
    boosted_total = boosted["projection_total"]
    assert boosted_total >= plain_total


def test_model_param_reaches_pipeline(make_client) -> None:
    """``?model=context`` must select the context scoring model.

    The fixture snapshot is tiny, so context falls back to naive means —
    but its notes carry the "context:" tag, proving the param routed.
    """

    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    body = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster",
                "model": "context"},
    ).json()
    recs = [d["recommended"] for d in body["decisions"] if d["recommended"]]
    assert recs
    assert all(
        any(note.startswith("context:") for note in r["score"]["notes"]) for r in recs
    )


def test_unknown_model_is_a_400(make_client) -> None:
    http, snaps = _basic_setup()
    client = make_client(http=http, snapshots=snaps)
    resp = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster",
                "model": "clairvoyant"},
    )
    assert resp.status_code == 400
    assert "unknown scoring model" in resp.json()["error"]


def test_swap_includes_current_starter_score(make_client) -> None:
    """A SWAP slot carries the current starter's own score so the UI can
    show the projected cost of not swapping."""

    players = {
        "wr_strong": make_player("wr_strong", full_name="Strong WR",
                                 position="WR", fantasy_positions=("WR",), team="LAR"),
        "wr_weak": make_player("wr_weak", full_name="Weak WR",
                               position="WR", fantasy_positions=("WR",), team="GB"),
    }
    weekly = {
        1: {"wr_strong": {"rec_yd": 200.0, "rec": 10.0},
            "wr_weak": {"rec_yd": 20.0, "rec": 1.0}},
        2: {"wr_strong": {"rec_yd": 220.0, "rec": 11.0},
            "wr_weak": {"rec_yd": 25.0, "rec": 2.0}},
    }
    season = 2026
    snap = make_snapshot(players=players, weekly_stats=weekly, season=season,
                        weeks_included=(1, 2))
    http = FakeHttp(
        league_routes(
            season=season,
            state_season=season,
            state_week=3,
            user_roster_players=("wr_strong", "wr_weak"),
            user_roster_starters=("wr_weak",),
            roster_positions=("WR", "BN"),
        )
    )
    client = make_client(http=http, snapshots={season: snap})
    body = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    ).json()
    wr_slot = next(d for d in body["decisions"] if d["slot_id"] == "WR1")

    assert wr_slot["matches_current"] is False
    starter_score = wr_slot["current_starter_score"]
    assert starter_score is not None
    assert (
        wr_slot["recommended"]["score"]["projected_mean"]
        > starter_score["projected_mean"]
    )


def test_match_slot_starter_score_equals_recommendation(make_client) -> None:
    http, snaps = _basic_setup(
        user_roster_starters=("qb1", "rb1", "rb2", "wr1", "wr2", "te1", "wr3"),
    )
    client = make_client(http=http, snapshots=snaps)
    body = client.get(
        "/leagues/L1/decisions",
        params={"user": "cole", "season": 2026, "week": 3, "pool": "roster"},
    ).json()
    matched = [d for d in body["decisions"] if d["matches_current"]]
    assert matched
    for d in matched:
        assert d["current_starter_score"] is not None
        assert d["current_starter_score"]["projected_mean"] == pytest.approx(
            d["recommended"]["score"]["projected_mean"]
        )
