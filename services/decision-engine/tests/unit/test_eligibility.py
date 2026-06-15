"""Tests for core.eligibility."""

from __future__ import annotations

import pytest

from decision_engine.core.eligibility import (
    UnsupportedSlotError,
    eligible_positions_for,
    player_eligible_for_slot,
)
from tests.unit.fakes import make_player


def test_exact_match_positions() -> None:
    assert eligible_positions_for("QB") == frozenset({"QB"})
    assert eligible_positions_for("rb") == frozenset({"RB"})


def test_flex_includes_rb_wr_te() -> None:
    assert eligible_positions_for("FLEX") == frozenset({"RB", "WR", "TE"})


def test_super_flex_includes_qb() -> None:
    assert eligible_positions_for("SUPER_FLEX") == frozenset({"QB", "RB", "WR", "TE"})


def test_wrrb_flex_excludes_te() -> None:
    assert eligible_positions_for("WRRB_FLEX") == frozenset({"RB", "WR"})


def test_bench_is_non_selectable() -> None:
    with pytest.raises(UnsupportedSlotError, match="not selectable"):
        eligible_positions_for("BN")


def test_unknown_slot_aborts_with_extension_message() -> None:
    with pytest.raises(UnsupportedSlotError, match="add it to the flex map"):
        eligible_positions_for("MEGA_FLEX")


def test_player_with_multi_position_eligible_for_flex() -> None:
    p = make_player("p1", fantasy_positions=("RB", "WR"))
    assert player_eligible_for_slot(p, "FLEX")
    assert player_eligible_for_slot(p, "RB")
    assert not player_eligible_for_slot(p, "QB")


def test_player_with_no_positions_not_eligible() -> None:
    p = make_player("p1", fantasy_positions=())
    assert not player_eligible_for_slot(p, "FLEX")
