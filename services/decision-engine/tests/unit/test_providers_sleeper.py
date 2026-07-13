"""Tests for providers.sleeper shape validators."""

from __future__ import annotations

import pytest

from decision_engine.providers.sleeper import (
    SchemaError,
    validate_league,
    validate_matchups,
    validate_rosters,
    validate_state,
    validate_user,
    validate_user_leagues,
)


def test_validate_state_happy_path() -> None:
    state = validate_state({"season": 2026, "week": 3})
    assert state.season == 2026
    assert state.week == 3


def test_validate_state_rejects_non_dict() -> None:
    with pytest.raises(SchemaError):
        validate_state([1, 2, 3])


def test_validate_user_happy_path() -> None:
    user = validate_user({"user_id": "U1", "username": "cole", "display_name": "Cole"})
    assert user.user_id == "U1"
    assert user.username == "cole"


def test_validate_user_rejects_missing_id() -> None:
    with pytest.raises(SchemaError, match="user_id"):
        validate_user({"username": "cole"})


def test_validate_user_leagues_skips_malformed_entries() -> None:
    """Quarantine: bad entries get dropped; valid ones come through."""

    leagues = validate_user_leagues(
        [
            {"league_id": "L1", "name": "Real", "season": "2026"},
            "not an object",
            {"name": "no id"},
            {"league_id": "L2", "name": "Other", "season": "2026"},
        ]
    )
    assert [lg.league_id for lg in leagues] == ["L1", "L2"]


def test_validate_user_leagues_rejects_non_list() -> None:
    with pytest.raises(SchemaError):
        validate_user_leagues({"foo": "bar"})


def test_validate_league_happy_path() -> None:
    league = validate_league(
        {
            "league_id": "L1",
            "name": "Test",
            "season": "2026",
            "roster_positions": ["QB", "RB", "WR", "FLEX"],
            "scoring_settings": {"rec": 1.0, "rec_yd": 0.1, "pass_yd": 0.04},
        }
    )
    assert league.roster_positions == ("QB", "RB", "WR", "FLEX")
    assert league.scoring_settings == {"rec": 1.0, "rec_yd": 0.1, "pass_yd": 0.04}


def test_validate_league_rejects_missing_roster_positions() -> None:
    with pytest.raises(SchemaError, match="roster_positions"):
        validate_league(
            {
                "league_id": "L1",
                "name": "Test",
                "season": "2026",
                "scoring_settings": {"rec": 1.0},
            }
        )


def test_validate_league_drops_non_numeric_scoring_weights() -> None:
    league = validate_league(
        {
            "league_id": "L1",
            "name": "Test",
            "season": "2026",
            "roster_positions": ["QB"],
            "scoring_settings": {"rec": 1.0, "bad": "not a number", "ok": 2},
        }
    )
    assert league.scoring_settings == {"rec": 1.0, "ok": 2.0}


def test_validate_rosters_quarantines_bad_entries() -> None:
    rosters = validate_rosters(
        [
            {"roster_id": 1, "owner_id": "U1", "players": ["p1", "p2"]},
            {"owner_id": "U2"},  # missing roster_id -> dropped
            "garbage",  # not a dict -> dropped
            {
                "roster_id": 3,
                "owner_id": "U3",
                "players": ["p3", 123, None],  # non-string entries dropped
                "starters": ["p3"],
            },
        ]
    )
    assert len(rosters) == 2
    assert rosters[0].players == ("p1", "p2")
    assert rosters[1].players == ("p3",)
    assert rosters[1].starters == ("p3",)


def test_validate_matchups_happy_path() -> None:
    matchups = validate_matchups(
        [
            {
                "roster_id": 1,
                "matchup_id": 7,
                "players": ["p1", "p2", "p3"],
                "starters": ["p1", "p2"],
                "points": 123.46,
            },
            {
                "roster_id": 2,
                "matchup_id": 7,
                "players": ["p4"],
                "starters": ["p4"],
                "points": 98,  # Sleeper sometimes ships ints
            },
        ]
    )
    assert len(matchups) == 2
    assert matchups[0].roster_id == 1
    assert matchups[0].starters == ("p1", "p2")
    assert matchups[0].points == 123.46
    assert matchups[1].points == 98.0


def test_validate_matchups_quarantines_bad_entries() -> None:
    matchups = validate_matchups(
        [
            {"roster_id": 1, "players": ["p1", 42, None], "starters": ["p1"]},
            {"players": ["p2"]},  # missing roster_id -> dropped
            "garbage",  # not a dict -> dropped
            {"roster_id": 3, "matchup_id": "x", "points": True},  # coerced to None
        ]
    )
    assert len(matchups) == 2
    assert matchups[0].players == ("p1",)
    assert matchups[1].matchup_id is None
    assert matchups[1].points is None
    assert matchups[1].players == ()


def test_validate_matchups_rejects_non_list() -> None:
    with pytest.raises(SchemaError):
        validate_matchups({"roster_id": 1})
