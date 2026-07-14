"""Optimal lineup assignment over predicted points (PRD 3.3). Pure.

Both lineup builders (the ``/decisions`` router and
``core/replay.py``) used to fill slots greedily in
``roster_positions`` order — wrong whenever a flexible slot precedes a
restrictive one (a league listing ``SUPER_FLEX`` before ``QB`` burns
its best QB in the flex and strands the QB slot). ``assign_lineup``
replaces that with exact assignment via the same bitmask DP
``replay.perfect_lineup_total`` uses; both share ``dp_best_assignment``
(same machinery, different objective: predicted vs actual points).
"""

from __future__ import annotations

from collections.abc import Callable

from decision_engine.core.eligibility import SLOT_ELIGIBILITY, player_eligible_for_slot
from decision_engine.types import Player, ScoredCandidate

# Above this many starter slots the exact search (bitmask DP over
# slots) stops being cheap; no real league gets close. Callers fall
# back to greedy rather than refuse — live leagues aren't sampled.
MAX_DP_SLOTS = 14


def dp_best_assignment(
    entries: list[tuple[str, list[int], float]],
    n_slots: int,
) -> tuple[int, float, dict[int, str]] | None:
    """Exact max-score assignment of players to slot bits.

    ``entries`` is ``(player_id, eligible_slot_bits, score)``, each
    player usable at most once, **in preference order**: on exact
    (filled, total) ties the earliest achiever wins, so callers sort by
    (-score, player_id) for deterministic output. Masks are visited
    descending so a player can't fill two slots in one pass. Prefers
    the fullest lineup, then the highest total — an empty slot never
    beats a startable player.

    Returns ``(filled_count, total, bit -> player_id)``; None when no
    entry fits any slot.
    """

    size = 1 << n_slots
    # dp[mask] = (filled, total); picks[mask] = ((bit, pid), ...).
    dp: list[tuple[int, float] | None] = [None] * size
    picks: list[tuple[tuple[int, str], ...]] = [()] * size
    dp[0] = (0, 0.0)
    for pid, bits, score in entries:
        for mask in range(size - 1, -1, -1):
            cur = dp[mask]
            if cur is None:
                continue
            cand = (cur[0] + 1, cur[1] + score)
            for b in bits:
                bit = 1 << b
                if mask & bit:
                    continue
                new = mask | bit
                prev = dp[new]
                if prev is None or cand > prev:
                    dp[new] = cand
                    picks[new] = (*picks[mask], (b, pid))

    best_mask = 0
    best: tuple[int, float] | None = None
    for mask in range(size):
        val = dp[mask]
        if val is None:
            continue
        if best is None or val > best:
            best = val
            best_mask = mask
    if best is None or best_mask == 0:
        return None
    return best[0], best[1], dict(picks[best_mask])


def assign_lineup(
    slots: list[str],
    candidates: dict[str, ScoredCandidate],
    eligible: Callable[[Player, str], bool] = player_eligible_for_slot,
) -> dict[str, str | None]:
    """Maximize total ``final_score`` over one-player-per-slot assignments.

    ``slots`` are the selectable slots in league order; keys of the
    result are slot ids in the callers' convention (``WR1``, ``FLEX2``
    — per-name counters over the selectable slots, which matches
    counters over the full ``roster_positions`` because non-selectable
    names are distinct). ``candidates`` is the shared score cache —
    assignment never re-scores.

    Ties: when the greedy fill (most-restrictive slots first) already
    achieves the DP optimum — every standard league, most weeks — its
    arrangement wins, keeping the intuitive "best WR in WR1" placement
    and making the result invariant to slot order. The DP arrangement
    is used only when it is strictly better (the superflex fix). Beyond
    ``MAX_DP_SLOTS`` slots the greedy fill is the answer.
    """

    slot_ids: list[str] = []
    seen: dict[str, int] = {}
    for slot in slots:
        seen[slot] = seen.get(slot, 0) + 1
        slot_ids.append(f"{slot}{seen[slot]}")

    # Deterministic preference order; also the DP tie-break order.
    ordered = sorted(
        candidates.values(),
        key=lambda c: (-c.final_score, c.player.player_id),
    )

    greedy = dict(zip(slot_ids, _greedy(slots, ordered, eligible), strict=True))
    if len(slots) > MAX_DP_SLOTS:
        return greedy

    # Prune: only the top ``len(slots)`` candidates per slot can appear
    # in an optimal assignment (exchange argument), which keeps the DP
    # small even for waiver-wide pools.
    keep: dict[str, ScoredCandidate] = {}
    for slot in slots:
        taken = 0
        for c in ordered:
            if taken >= len(slots):
                break
            if eligible(c.player, slot):
                keep[c.player.player_id] = c
                taken += 1

    entries: list[tuple[str, list[int], float]] = []
    for c in sorted(keep.values(), key=lambda c: (-c.final_score, c.player.player_id)):
        bits = [b for b, slot in enumerate(slots) if eligible(c.player, slot)]
        if bits:
            entries.append((c.player.player_id, bits, c.final_score))

    result = dp_best_assignment(entries, len(slots))
    if result is None:
        return greedy

    dp_count, dp_total, by_bit = result
    greedy_picks = [pid for pid in greedy.values() if pid is not None]
    greedy_count = len(greedy_picks)
    greedy_total = sum(candidates[pid].final_score for pid in greedy_picks)
    if (greedy_count, greedy_total) >= (dp_count, dp_total - 1e-9):
        return greedy

    assignment: dict[str, str | None] = {sid: None for sid in slot_ids}
    for b, pid in by_bit.items():
        assignment[slot_ids[b]] = pid
    return assignment


def _greedy(
    slots: list[str],
    ordered: list[ScoredCandidate],
    eligible: Callable[[Player, str], bool],
) -> list[str | None]:
    """Most-restrictive slots first, best remaining candidate per slot.

    Restrictiveness = how many positions the slot accepts, so single-
    position slots (QB/WR/K/…) fill before flexes and the result is
    invariant to the league's slot ordering. Ties (WR1 vs WR2) resolve
    in league order.
    """

    order = sorted(
        range(len(slots)),
        key=lambda i: (
            len(SLOT_ELIGIBILITY.get(slots[i].upper(), frozenset({slots[i]}))),
            i,
        ),
    )
    used: set[str] = set()
    out: list[str | None] = [None] * len(slots)
    for i in order:
        pick = next(
            (
                c.player.player_id
                for c in ordered
                if c.player.player_id not in used and eligible(c.player, slots[i])
            ),
            None,
        )
        if pick is not None:
            used.add(pick)
        out[i] = pick
    return out
