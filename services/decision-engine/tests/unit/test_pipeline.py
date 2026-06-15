"""End-to-end tests for core.pipeline.run with FakeHttp + FakeSnapshotReader."""

from __future__ import annotations

import pytest

from decision_engine.core import pipeline
from decision_engine.core.eligibility import UnsupportedSlotError
from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.pipeline import DecideRequest
from decision_engine.types import NflState, SnapshotData
from tests.unit.fakes import FakeHttp, FakeSnapshotReader, league_routes, make_player, make_snapshot


def _make_request(**overrides: object) -> DecideRequest:
    base: dict[str, object] = dict(
        user="cole",
        league_id="L1",
        slot="FLEX",
        risk=0.5,
        pool="roster",
        limit=10,
        model="naive",
        prefer_team=None,
        avoid_team=None,
        state_override=NflState(season=2026, week=3),
    )
    base.update(overrides)
    return DecideRequest(**base)  # type: ignore[arg-type]


def _snapshot_with_history() -> SnapshotData:
    """Snapshot with 3 weeks of stats for two players the user rosters."""

    players = {
        "p1": make_player("p1", full_name="A WR", fantasy_positions=("WR",), team="DET"),
        "p2": make_player("p2", full_name="A RB", fantasy_positions=("RB",), team="CHI"),
        # On other roster — only used in waivers/both tests.
        "p3": make_player("p3", full_name="Other WR", fantasy_positions=("WR",), team="KC"),
        "p4": make_player("p4", full_name="Other RB", fantasy_positions=("RB",), team="KC"),
        # Free agent — never rostered.
        "p9": make_player("p9", full_name="Free WR", fantasy_positions=("WR",), team="MIA"),
    }
    weekly = {
        1: {
            "p1": {"rec_yd": 100.0, "rec": 5.0},
            "p2": {"rush_yd": 80.0},
            "p3": {"rec_yd": 60.0},
            "p4": {"rush_yd": 100.0},
            "p9": {"rec_yd": 90.0},
        },
        2: {
            "p1": {"rec_yd": 80.0, "rec": 4.0},
            "p2": {"rush_yd": 90.0},
            "p3": {"rec_yd": 50.0},
            "p4": {"rush_yd": 110.0},
            "p9": {"rec_yd": 70.0},
        },
        3: {
            "p1": {"rec_yd": 110.0, "rec": 6.0},
            "p2": {"rush_yd": 70.0},
            "p3": {"rec_yd": 40.0},
            "p4": {"rush_yd": 95.0},
            "p9": {"rec_yd": 80.0},
        },
    }
    return make_snapshot(
        players=players,
        weekly_stats=weekly,
        weeks_included=(1, 2, 3),
        season=2026,
    )


def test_roster_pool_scores_only_user_players() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="roster"),
    )

    pids = {c.player.player_id for c in result.candidates}
    assert pids == {"p1", "p2"}
    assert all(c.on_user_roster for c in result.candidates)


def test_waivers_pool_excludes_all_rostered_players() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(
        league_routes(
            user_roster_players=("p1", "p2"),
            other_roster_players=("p3", "p4"),
        )
    )
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="waivers"),
    )

    pids = {c.player.player_id for c in result.candidates}
    assert "p9" in pids
    assert not (pids & {"p1", "p2", "p3", "p4"})


def test_both_pool_includes_user_roster_and_waivers_but_not_others() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(
        league_routes(
            user_roster_players=("p1", "p2"),
            other_roster_players=("p3", "p4"),
        )
    )
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="both"),
    )

    pids = {c.player.player_id for c in result.candidates}
    assert {"p1", "p2", "p9"} <= pids
    assert "p3" not in pids
    assert "p4" not in pids


def test_results_sorted_by_final_score_desc() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="roster"),
    )

    scores = [c.final_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_prefer_team_boosts_score_by_10_pct() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    baseline = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="roster"),
    )
    p1_baseline = next(c for c in baseline.candidates if c.player.player_id == "p1")

    http2 = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    boosted = pipeline.run(
        http=http2,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="roster", prefer_team="DET"),
    )
    p1_boosted = next(c for c in boosted.candidates if c.player.player_id == "p1")

    assert p1_boosted.preference_note is not None
    assert p1_boosted.preference_note.startswith("+10%")
    # 10% boost over baseline final_score.
    assert p1_boosted.final_score == pytest.approx(p1_baseline.final_score * 1.10)


def test_avoid_team_penalises_score_by_10_pct() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", pool="roster", avoid_team="CHI"),
    )
    p2 = next(c for c in result.candidates if c.player.player_id == "p2")
    assert p2.preference_note is not None
    assert p2.preference_note.startswith("-10%")


def test_unknown_user_surfaces_user_input_error() -> None:
    """404 on the user lookup -> UserInputError (CLI exit 1)."""

    from decision_engine.clients.http import NotFoundError

    routes = league_routes()
    routes["/v1/user/cole"] = NotFoundError("/v1/user/cole: 404")
    http = FakeHttp(routes)
    reader = FakeSnapshotReader(_snapshot_with_history())

    with pytest.raises(UserInputError, match="unknown Sleeper username"):
        pipeline.run(
            http=http,
            snapshot_reader=reader,
            request=_make_request(),
        )


def test_league_mismatch_lists_available_leagues() -> None:
    """League ID not in user's leagues -> UserInputError listing them."""

    snap = _snapshot_with_history()
    http = FakeHttp(league_routes())
    reader = FakeSnapshotReader(snap)

    with pytest.raises(UserInputError, match="Available leagues"):
        pipeline.run(
            http=http,
            snapshot_reader=reader,
            request=_make_request(league_id="BADLEAGUE"),
        )


def test_bench_slot_rejected_with_helpful_error() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes())
    reader = FakeSnapshotReader(snap)

    with pytest.raises(UnsupportedSlotError):
        pipeline.run(
            http=http,
            snapshot_reader=reader,
            request=_make_request(slot="BN"),
        )


def test_limit_caps_result_length() -> None:
    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(slot="FLEX", limit=1),
    )
    assert len(result.candidates) == 1


def test_exclude_player_ids_drops_them_from_candidates() -> None:
    """``exclude_player_ids`` keeps players out of the candidate list.

    The /decisions endpoint uses this to prevent the same player from
    being recommended into multiple slots (WR1, WR2, FLEX). Regression
    test for the duplicate-lineup-slot bug.
    """

    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    excluded = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(
            slot="FLEX",
            pool="roster",
            exclude_player_ids=frozenset({"p1"}),
        ),
    )
    pids = {c.player.player_id for c in excluded.candidates}
    assert "p1" not in pids
    assert "p2" in pids


def test_exclude_all_eligible_yields_empty_candidates() -> None:
    """If every eligible player is excluded, the result is empty.

    Models the lineup case where a starter slot has no remaining
    unique pick — caller (decisions router) must handle that gracefully
    rather than crashing or recycling.
    """

    snap = _snapshot_with_history()
    http = FakeHttp(league_routes(user_roster_players=("p1", "p2")))
    reader = FakeSnapshotReader(snap)

    result = pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(
            slot="FLEX",
            pool="roster",
            exclude_player_ids=frozenset({"p1", "p2"}),
        ),
    )
    assert result.candidates == ()


def test_state_override_skips_state_call() -> None:
    snap = _snapshot_with_history()
    routes = league_routes()
    # No /v1/state/nfl route — fake would raise if pipeline called it.
    assert "/v1/state/nfl" not in routes
    http = FakeHttp(routes)
    reader = FakeSnapshotReader(snap)

    pipeline.run(
        http=http,
        snapshot_reader=reader,
        request=_make_request(state_override=NflState(season=2026, week=3)),
    )
    assert "/v1/state/nfl" not in http.calls
