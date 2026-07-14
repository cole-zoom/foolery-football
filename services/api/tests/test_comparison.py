"""Contracts for ``GET /leagues/{id}/comparison``.

The load-bearing behaviours:

- Predictions replay leakage-safe (model sees weeks < W); actuals come
  from week W itself, scored with the league's weights.
- The candidate pool and the "human" starters are the *matchup archive*
  roster for week W, not the live roster — mid-season roster churn must
  not leak into the replay.
- Totals are consistent with the per-slot rows; the perfect-hindsight
  total is the best assignment of that week's roster to the slots.
- A week with no recorded stats is a 400, not a silent zero comparison.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    FakeHttp,
    league_routes,
    make_player,
    make_snapshot,
)

SEASON = 2026
SCORING = {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1}


def _players() -> dict[str, object]:
    return {
        "wr_strong": make_player("wr_strong", full_name="Strong WR",
                                 position="WR", fantasy_positions=("WR",), team="LAR"),
        "wr_weak": make_player("wr_weak", full_name="Weak WR",
                               position="WR", fantasy_positions=("WR",), team="GB"),
        "rb1": make_player("rb1", full_name="RB One",
                           position="RB", fantasy_positions=("RB",), team="PIT"),
        # Off the user's roster; only reachable through pool=waivers/both.
        "wr_fa": make_player("wr_fa", full_name="Wire WR",
                             position="WR", fantasy_positions=("WR",), team="KC"),
        "wr_taken": make_player("wr_taken", full_name="Taken WR",
                                position="WR", fantasy_positions=("WR",), team="SF"),
    }


def _weekly() -> dict[int, dict[str, dict[str, float]]]:
    # Weeks 1-2 train the model; week 3 is the comparison target. The
    # strong WR has the big history AND the big week 3; the weak WR was
    # started by the human and flopped. rb1 idle in week 3 (didn't play).
    return {
        1: {"wr_strong": {"rec_yd": 200.0, "rec": 10.0},
            "wr_weak": {"rec_yd": 20.0, "rec": 1.0},
            "rb1": {"rush_yd": 80.0},
            "wr_fa": {"rec_yd": 350.0, "rec": 5.0},
            "wr_taken": {"rec_yd": 450.0, "rec": 5.0}},
        2: {"wr_strong": {"rec_yd": 220.0, "rec": 11.0},
            "wr_weak": {"rec_yd": 25.0, "rec": 2.0},
            "rb1": {"rush_yd": 90.0},
            "wr_fa": {"rec_yd": 350.0, "rec": 5.0},
            "wr_taken": {"rec_yd": 450.0, "rec": 5.0}},
        3: {"wr_strong": {"rec_yd": 150.0, "rec": 8.0},
            "wr_weak": {"rec_yd": 10.0, "rec": 1.0},
            "wr_fa": {"rec_yd": 100.0, "rec": 5.0},
            "wr_taken": {"rec_yd": 250.0, "rec": 5.0}},
    }


def _routes(
    *,
    matchup_players: tuple[str, ...] = ("wr_strong", "wr_weak", "rb1"),
    matchup_starters: tuple[str, ...] = ("wr_weak",),
    roster_positions: tuple[str, ...] = ("WR", "BN", "BN"),
    current_roster: tuple[str, ...] = ("wr_strong", "wr_weak", "rb1"),
    other_current_roster: tuple[str, ...] = (),
    other_matchup_players: tuple[str, ...] = ("someone_else",),
) -> dict[str, object]:
    routes = league_routes(
        season=SEASON,
        state_season=SEASON,
        state_week=5,
        user_roster_players=current_roster,
        user_roster_starters=matchup_starters,
        other_roster_players=other_current_roster,
        roster_positions=roster_positions,
        scoring_settings=dict(SCORING),
    )
    routes["/v1/league/L1/matchups/3"] = [
        {
            "roster_id": 1,
            "matchup_id": 1,
            "players": list(matchup_players),
            "starters": list(matchup_starters),
            "points": 11.0,
        },
        {
            "roster_id": 2,
            "matchup_id": 1,
            "players": list(other_matchup_players),
            "starters": list(other_matchup_players),
            "points": 50.0,
        },
    ]
    return routes


def _get(client, **params):
    base = {"user": "cole", "season": SEASON, "week": 3}
    return client.get("/leagues/L1/comparison", params={**base, **params})


def _snapshots():
    return {
        SEASON: make_snapshot(
            players=_players(),
            weekly_stats=_weekly(),
            season=SEASON,
            weeks_included=(1, 2, 3),
        )
    }


def test_model_pick_vs_actual_starter(make_client) -> None:
    """Model replays the strong WR; the human really started the weak one.
    Actuals for both come from week 3 under league scoring."""

    client = make_client(http=FakeHttp(_routes()), snapshots=_snapshots())
    body = _get(client).json()

    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    assert wr["model_pick"]["player"]["player_id"] == "wr_strong"
    assert wr["actual_starter"]["player"]["player_id"] == "wr_weak"
    assert wr["same_player"] is False
    # week 3 actuals: strong = 150*0.1 + 8 = 23.0; weak = 10*0.1 + 1 = 2.0
    assert wr["model_pick"]["actual_points"] == pytest.approx(23.0)
    assert wr["actual_starter"]["actual_points"] == pytest.approx(2.0)
    # Prediction must be leakage-safe: weeks 1-2 only. Naive mean of the
    # strong WR = (30 + 33) / 2 = 31.5.
    assert wr["model_pick"]["predicted_mean"] == pytest.approx(31.5)

    totals = body["totals"]
    assert totals["model_actual"] == pytest.approx(23.0)
    assert totals["human_actual"] == pytest.approx(2.0)
    # Perfect hindsight for one WR slot = the strong WR's 23.0.
    assert totals["perfect_actual"] == pytest.approx(23.0)


def test_pool_is_the_matchup_roster_not_the_current_one(make_client) -> None:
    """The strong WR was traded away after week 3: he's on the *current*
    roster response but absent from the week-3 matchup. The model must
    not be allowed to pick him."""

    routes = _routes(matchup_players=("wr_weak", "rb1"))
    client = make_client(http=FakeHttp(routes), snapshots=_snapshots())
    body = _get(client).json()

    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    assert wr["model_pick"]["player"]["player_id"] == "wr_weak"
    assert wr["same_player"] is True
    # The accuracy table covers the week-3 roster only.
    roster_ids = {r["player"]["player_id"] for r in body["roster"]}
    assert roster_ids == {"wr_weak", "rb1"}


def test_accuracy_counts_only_players_who_played(make_client) -> None:
    """rb1 has no week-3 stat row (didn't play): his actual is null and
    he's excluded from MAE, which averages predicted-vs-actual errors."""

    client = make_client(http=FakeHttp(_routes()), snapshots=_snapshots())
    body = _get(client).json()

    by_pid = {r["player"]["player_id"]: r for r in body["roster"]}
    assert by_pid["rb1"]["actual_points"] is None
    assert by_pid["rb1"]["predicted_mean"] is None  # no RB slot to score him in

    # Errors: strong 31.5 - 23.0 = 8.5; weak ((3+4.5)/2=3.75) - 2.0 = 1.75.
    acc = body["accuracy"]
    assert acc["n"] == 2
    assert acc["mae"] == pytest.approx((8.5 + 1.75) / 2)
    assert acc["mean_error"] == pytest.approx((8.5 + 1.75) / 2)


def test_pool_both_draws_from_the_week_w_waiver_wire(make_client) -> None:
    """pool=both lets the model raid the wire, judged by *week-3* rosters:
    wr_taken sat on the other team's week-3 matchup roster (untouchable,
    despite the biggest projection and being off that roster *today*),
    while wr_fa — on the other team's roster today but a free agent in
    week 3 — is fair game and out-projects the user's own WRs."""

    routes = _routes(
        other_current_roster=("wr_fa", "someone_else"),
        other_matchup_players=("wr_taken",),
    )
    client = make_client(http=FakeHttp(routes), snapshots=_snapshots())

    body = _get(client, pool="both").json()
    assert body["pool"] == "both"
    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    # wr_fa predicted (35+5)=40 beats wr_strong's 31.5; wr_taken's 50
    # never enters the pool.
    assert wr["model_pick"]["player"]["player_id"] == "wr_fa"
    assert wr["model_pick"]["predicted_mean"] == pytest.approx(40.0)
    # The human side is untouched by the pool knob.
    assert wr["actual_starter"]["player"]["player_id"] == "wr_weak"

    # Default pool stays roster-only.
    body = _get(client).json()
    assert body["pool"] == "roster"
    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    assert wr["model_pick"]["player"]["player_id"] == "wr_strong"


def test_pool_waivers_keeps_the_report_card_projected(make_client) -> None:
    """Waivers-only removes the user's players from the model's pool, but
    the actual starter's projection, the human total, and the accuracy
    stats must still be computed for the roster."""

    client = make_client(http=FakeHttp(_routes()), snapshots=_snapshots())
    body = _get(client, pool="waivers").json()

    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    # Both free agents are on the wire in week 3; wr_taken projects higher.
    assert wr["model_pick"]["player"]["player_id"] == "wr_taken"
    assert wr["model_pick"]["predicted_mean"] == pytest.approx(50.0)
    # The human starter still carries a projection even though the model's
    # pool never scored him: naive mean of wr_weak weeks 1-2 = 3.75.
    assert wr["actual_starter"]["predicted_mean"] == pytest.approx(3.75)
    assert body["totals"]["human_predicted"] == pytest.approx(3.75)
    # Report card unchanged from the roster-pool runs.
    acc = body["accuracy"]
    assert acc["n"] == 2
    assert acc["mae"] == pytest.approx((8.5 + 1.75) / 2)


def test_uncompleted_week_is_a_400(make_client) -> None:
    client = make_client(http=FakeHttp(_routes()), snapshots=_snapshots())
    resp = _get(client, week=4)
    assert resp.status_code == 400
    assert "no recorded stats" in resp.json()["error"]


def test_missing_matchup_is_a_400(make_client) -> None:
    routes = _routes()
    routes["/v1/league/L1/matchups/3"] = [
        {"roster_id": 2, "players": ["someone_else"], "starters": ["someone_else"]},
    ]
    client = make_client(http=FakeHttp(routes), snapshots=_snapshots())
    resp = _get(client)
    assert resp.status_code == 400
    assert "matchup" in resp.json()["error"]


def test_bye_starter_unprojected_and_model_picks_playing_player(make_client) -> None:
    """The human started the strong WR on his bye week (LAR idle in the
    schedule, no week-3 stat row). The model must field the playing WR
    instead, and the bye starter carries neither projection nor actual."""

    routes = _routes(matchup_starters=("wr_strong",))
    weekly = _weekly()
    del weekly[3]["wr_strong"]  # bye: no stat row that week
    snap = make_snapshot(
        players=_players(),
        weekly_stats=weekly,
        season=SEASON,
        weeks_included=(1, 2, 3),
        schedule={3: {"GB": "CHI", "CHI": "GB", "PIT": "DAL", "DAL": "PIT"}},
    )
    client = make_client(http=FakeHttp(routes), snapshots={SEASON: snap})
    body = _get(client).json()

    wr = next(s for s in body["slots"] if s["slot_id"] == "WR1")
    assert wr["model_pick"]["player"]["player_id"] == "wr_weak"
    assert wr["actual_starter"]["player"]["player_id"] == "wr_strong"
    assert wr["actual_starter"]["predicted_mean"] is None
    assert wr["actual_starter"]["actual_points"] is None


def test_perfect_lineup_respects_eligibility_and_uniqueness(make_client) -> None:
    """Two WR slots + FLEX: perfect total must not reuse a player and must
    put the best eligible actuals in each slot."""

    routes = _routes(
        matchup_starters=("wr_weak", "wr_strong", "rb1"),
        roster_positions=("WR", "WR", "FLEX", "BN"),
    )
    client = make_client(http=FakeHttp(routes), snapshots=_snapshots())
    body = _get(client).json()

    # Week-3 actuals: strong 23.0, weak 2.0, rb1 didn't play. Perfect =
    # strong + weak in the WR slots, FLEX left unfillable (rb1 no row).
    assert body["totals"]["perfect_actual"] == pytest.approx(25.0)
