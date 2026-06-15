"""Slot eligibility map. Pure.

The source of truth is the table in
``docs/references/fantasy-glossary.md`` (§ "Slot eligibility map"). Any
new league flex variant must land in both places.
"""

from __future__ import annotations

from typing import Final

from decision_engine.types import Player

# Slot name -> the set of ``fantasy_positions`` codes that may fill it.
# Slot names are case-normalised to upper-case by the caller.
SLOT_ELIGIBILITY: Final[dict[str, frozenset[str]]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DEF"}),
    "DST": frozenset({"DEF"}),
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "WRT_FLEX": frozenset({"RB", "WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}

# Slots that exist in ``roster_positions`` but can never be filled by a
# ``decide --slot`` call. PRD 2.1 requires we exit 1 if the user asks
# for one of these.
NON_SELECTABLE_SLOTS: Final[frozenset[str]] = frozenset({"BN", "IR", "TAXI"})


class UnsupportedSlotError(ValueError):
    """``--slot`` value isn't in the eligibility map. Add it there."""


def eligible_positions_for(slot: str) -> frozenset[str]:
    """Return the set of ``fantasy_positions`` that may fill ``slot``.

    Raises ``UnsupportedSlotError`` for non-selectable slots (BN/IR/TAXI)
    and anything not in the map.
    """

    s = slot.upper()
    if s in NON_SELECTABLE_SLOTS:
        raise UnsupportedSlotError(
            f"slot {s!r} is not selectable (bench/IR/taxi). "
            "Pick QB/RB/WR/TE/FLEX/etc."
        )
    if s not in SLOT_ELIGIBILITY:
        raise UnsupportedSlotError(
            f"unsupported slot {s!r} — add it to the flex map "
            "in core/eligibility.py + fantasy-glossary.md."
        )
    return SLOT_ELIGIBILITY[s]


def player_eligible_for_slot(player: Player, slot: str) -> bool:
    """True iff any of ``player.fantasy_positions`` can fill ``slot``."""

    allowed = eligible_positions_for(slot)
    return any(pos in allowed for pos in player.fantasy_positions)
