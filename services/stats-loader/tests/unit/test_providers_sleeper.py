"""Tests for providers.sleeper validators."""

from __future__ import annotations

import pytest

from stats_loader.providers import sleeper
from stats_loader.providers.sleeper import SchemaError
from tests.unit.fakes import make_players, make_weekly


def test_validate_state_accepts_well_formed() -> None:
    state = sleeper.validate_state({"season": 2026, "week": 3, "extra": "field"})
    assert state.season == 2026
    assert state.week == 3


def test_validate_state_rejects_non_dict() -> None:
    with pytest.raises(SchemaError):
        sleeper.validate_state("nope")


def test_validate_state_rejects_missing_field() -> None:
    with pytest.raises(SchemaError):
        sleeper.validate_state({"season": 2026})


def test_validate_players_accepts_real_size() -> None:
    payload = make_players(count=1200)
    assert sleeper.validate_players(payload) is payload


def test_validate_players_rejects_too_few() -> None:
    with pytest.raises(SchemaError, match="too few players"):
        sleeper.validate_players(make_players(count=10))


def test_validate_players_rejects_universal_field_loss() -> None:
    payload = make_players(count=1200)
    for entry in payload.values():
        entry.pop("full_name")
    with pytest.raises(SchemaError, match="full_name"):
        sleeper.validate_players(payload)


def test_validate_players_tolerates_per_entry_field_loss() -> None:
    # Only one entry missing `position` — defenses / retired players —
    # is fine. The decision engine filters them.
    payload = make_players(count=1200)
    first_pid = next(iter(payload))
    payload[first_pid].pop("position")
    assert sleeper.validate_players(payload) is payload


def test_validate_players_rejects_missing_player_id() -> None:
    payload = make_players(count=1200)
    first_pid = next(iter(payload))
    payload[first_pid].pop("player_id")
    with pytest.raises(SchemaError, match="missing string player_id"):
        sleeper.validate_players(payload)


def test_validate_weekly_rejects_empty_past_week() -> None:
    with pytest.raises(SchemaError, match="empty response"):
        sleeper.validate_weekly({}, label="stats_week_3", allow_empty=False)


def test_validate_weekly_allows_empty_current_week() -> None:
    assert sleeper.validate_weekly({}, label="proj_week_5", allow_empty=True) == {}


def test_validate_weekly_keeps_payload_verbatim() -> None:
    payload = make_weekly(["1000", "1001"])
    assert sleeper.validate_weekly(payload, label="x", allow_empty=False) is payload
