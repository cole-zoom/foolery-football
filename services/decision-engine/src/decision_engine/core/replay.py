"""Leakage-safe one-week lineup replay — model vs the human vs hindsight.

For a completed week W this replays a model's recommended lineup under
the same leakage-safe contract as a live decide (scoring sees only
weeks before W), pulls the lineup the manager *actually* fielded from
Sleeper's matchup archive, and scores both against what really happened
using the league's own scoring weights.

Historical fidelity: the candidate pool and starters both come from the
week-W matchup entries, not from ``/rosters`` — Sleeper's roster
endpoint only returns *current* state, so mid-season trades and pickups
would otherwise leak into the replay. That holds for ``pool=waivers``/
``both`` too: the free-agent set is everyone outside the week-W matchup
rosters, league-wide.

Shared by the API comparison router (which decorates the result with
wire-format player rows) and the offline eval harness under ``evals/``.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

from decision_engine.clients.http import HttpClient
from decision_engine.clients.snapshot_reader import SnapshotReader
from decision_engine.core import pipeline
from decision_engine.core.eligibility import (
    NON_SELECTABLE_SLOTS,
    player_eligible_for_slot,
)
from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.pipeline import DecideRequest
from decision_engine.core.scoring.common import weekly_points
from decision_engine.types import (
    LeagueContext,
    Matchup,
    NflState,
    Pool,
    ScoredCandidate,
    ScoringSettings,
    SnapshotData,
)

# Above this many starter slots the exact perfect-lineup search (bitmask
# DP over slots) stops being cheap; no real league gets close.
PERFECT_LINEUP_MAX_SLOTS = 14

DEFAULT_CANDIDATE_LIMIT = 10_000


@dataclasses.dataclass(frozen=True, slots=True)
class SlotPick:
    """One selectable slot: who the model picked vs who actually started."""

    slot_id: str
    slot: str
    model_player_id: str | None
    human_player_id: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class WeekComparison:
    """Everything a caller needs to report on one replayed week.

    ``league_context`` is the week-swapped context (rosters/starters as
    they stood in week W), so callers can walk ``user_roster.players``
    for per-player reporting. ``predicted_mean`` is the pooled
    ``projected_mean`` per player_id across every slot run — a player's
    mean is slot-independent, so first sighting wins.
    """

    season: int
    week: int
    model: str
    league_context: LeagueContext
    slot_picks: tuple[SlotPick, ...]
    selectable_slots: tuple[str, ...]
    predicted_mean: dict[str, float]
    model_predicted: float
    model_actual: float
    human_predicted: float | None
    human_actual: float
    perfect_actual: float | None
    using_prior_season: bool
    prior_season: int | None


def replay_week_comparison(
    *,
    http: HttpClient,
    snapshot_reader: SnapshotReader,
    snapshot: SnapshotData,
    league_context: LeagueContext,
    matchups: list[Matchup],
    season: int,
    week: int,
    model: str,
    risk: float = 0.5,
    pool: Pool = "roster",
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> WeekComparison:
    """Replay week ``week`` for the context's roster under ``model``.

    ``snapshot`` is the full-season snapshot — the pipeline trims it per
    week. ``matchups`` is the week-W archive from ``fetch_matchups``.
    Raises ``UserInputError`` when the week has no recorded stats or the
    user's roster has no matchup entry.
    """

    actual_table = snapshot.weekly_stats.get(week)
    if not actual_table:
        raise UserInputError(
            f"week {week} of {season} has no recorded stats yet — "
            "the comparison needs a completed week"
        )

    scoring = league_context.league.scoring_settings

    matchup = next(
        (m for m in matchups if m.roster_id == league_context.user_roster.roster_id),
        None,
    )
    if matchup is None:
        raise UserInputError(
            f"no week-{week} matchup found for roster "
            f"{league_context.user_roster.roster_id} in league "
            f"{league_context.league.league_id} — the league may not have "
            "played that week"
        )

    # Swap in the week-W roster/starters so both the model's pool and the
    # "human" baseline are what actually existed that week. Every roster
    # is rebuilt, not just the user's: pool=waivers/both derives the free
    # agent set from all_rostered_player_ids, which must reflect who was
    # rostered *that week* — a player another team dropped since then was
    # not on the wire in week W, and one they picked up since was. Empty
    # fields (very old leagues) fall back to the live roster rather than
    # abort.
    matchup_by_roster = {m.roster_id: m for m in matchups}
    week_rosters = tuple(
        r.model_copy(
            update={
                "players": m.players or r.players,
                "starters": m.starters or r.starters,
            }
        )
        if (m := matchup_by_roster.get(r.roster_id)) is not None
        else r
        for r in league_context.rosters
    )
    week_roster = league_context.user_roster.model_copy(
        update={
            "players": matchup.players or league_context.user_roster.players,
            "starters": matchup.starters or league_context.user_roster.starters,
        }
    )
    league_context = league_context.model_copy(
        update={"rosters": week_rosters, "user_roster": week_roster}
    )

    state_override = NflState(season=season, week=week)

    # projected_mean per player_id, pooled across every slot run — fills
    # in predictions for actual starters even when a slot run excluded
    # them (already assigned to an earlier slot).
    predicted: dict[str, float] = {}

    starters = list(week_roster.starters)
    seen: Counter[str] = Counter()
    assigned_player_ids: set[str] = set()
    # Scores are slot-independent; share them across the slot loop.
    score_cache: dict[str, ScoredCandidate] = {}
    slot_picks: list[SlotPick] = []
    selectable_slots: list[str] = []
    using_prior_season = False
    prior_season: int | None = None

    for i, slot in enumerate(league_context.league.roster_positions):
        seen[slot] += 1
        slot_id = f"{slot}{seen[slot]}"
        if slot in NON_SELECTABLE_SLOTS:
            continue
        selectable_slots.append(slot)

        request = DecideRequest(
            user=league_context.user.user_id,
            league_id=league_context.league.league_id,
            slot=slot,
            risk=risk,
            pool=pool,
            limit=candidate_limit,
            model=model,
            prefer_team=None,
            avoid_team=None,
            state_override=state_override,
            exclude_player_ids=frozenset(assigned_player_ids),
        )
        result = pipeline.run(
            http=http,
            snapshot_reader=snapshot_reader,
            request=request,
            snapshot=snapshot,
            league_context=league_context,
            score_cache=score_cache,
        )
        if result.using_prior_season:
            using_prior_season = True
            prior_season = result.prior_season

        for c in result.candidates:
            predicted.setdefault(c.player.player_id, c.score.projected_mean)

        if pool == "waivers":
            # Waivers-only drops the user's own players from the run, but
            # the "you" column and the report card still need their
            # projections — harvest them from a roster-pool pass. The
            # shared score cache makes this nearly free, and nothing from
            # it feeds the model's pick.
            roster_result = pipeline.run(
                http=http,
                snapshot_reader=snapshot_reader,
                request=dataclasses.replace(
                    request,
                    pool="roster",
                    exclude_player_ids=frozenset(),
                ),
                snapshot=snapshot,
                league_context=league_context,
                score_cache=score_cache,
            )
            for c in roster_result.candidates:
                predicted.setdefault(c.player.player_id, c.score.projected_mean)

        top = result.candidates[0] if result.candidates else None
        if top is not None:
            assigned_player_ids.add(top.player.player_id)

        starter_pid = starters[i] if i < len(starters) else None
        slot_picks.append(
            SlotPick(
                slot_id=slot_id,
                slot=slot,
                model_player_id=top.player.player_id if top else None,
                human_player_id=starter_pid,
            )
        )

    def actual_points_of(player_id: str | None) -> float | None:
        """None when the player is unknown to the snapshot or scoreless."""

        if not player_id or player_id not in snapshot.players:
            return None
        stats = actual_table.get(player_id)
        return weekly_points(stats, scoring) if stats else None

    model_predicted = 0.0
    model_actual = 0.0
    human_predicted_parts: list[float] = []
    human_actual = 0.0
    for pick in slot_picks:
        # A pick whose player is missing from the snapshot contributes
        # nothing, prediction included — same rule the wire rows follow.
        if pick.model_player_id and pick.model_player_id in snapshot.players:
            model_predicted += predicted.get(pick.model_player_id) or 0.0
            model_actual += actual_points_of(pick.model_player_id) or 0.0
        if pick.human_player_id and pick.human_player_id in snapshot.players:
            starter_predicted = predicted.get(pick.human_player_id)
            if starter_predicted is not None:
                human_predicted_parts.append(starter_predicted)
            human_actual += actual_points_of(pick.human_player_id) or 0.0

    return WeekComparison(
        season=season,
        week=week,
        model=model,
        league_context=league_context,
        slot_picks=tuple(slot_picks),
        selectable_slots=tuple(selectable_slots),
        predicted_mean=predicted,
        model_predicted=model_predicted,
        model_actual=model_actual,
        human_predicted=(
            sum(human_predicted_parts) if human_predicted_parts else None
        ),
        human_actual=human_actual,
        perfect_actual=perfect_lineup_total(
            selectable_slots,
            week_roster.players,
            snapshot,
            actual_table,
            scoring,
        ),
        using_prior_season=using_prior_season,
        prior_season=prior_season,
    )


def perfect_lineup_total(
    slots: list[str],
    roster_player_ids: tuple[str, ...],
    snapshot: SnapshotData,
    actual_table: dict[str, dict[str, float]],
    scoring: ScoringSettings,
) -> float | None:
    """Best actual total the roster could have produced with hindsight.

    Exact assignment via DP over the bitmask of filled slots: for each
    player (used at most once) try every eligible still-empty slot.
    Masks are visited descending so a player can't fill two slots in the
    same pass. Prefers a full lineup when one exists — an empty slot is
    only "better" when a starter scored negative, which isn't a lineup a
    manager could field.
    """

    n = len(slots)
    if n == 0 or n > PERFECT_LINEUP_MAX_SLOTS:
        return None

    scored: list[tuple[list[int], float]] = []
    for pid in roster_player_ids:
        player = snapshot.players.get(pid)
        stats = actual_table.get(pid)
        if player is None or not stats:
            continue
        eligible_bits = [
            b for b, slot in enumerate(slots) if player_eligible_for_slot(player, slot)
        ]
        if not eligible_bits:
            continue
        scored.append((eligible_bits, weekly_points(stats, scoring)))

    if not scored:
        return None

    neg_inf = float("-inf")
    dp = [neg_inf] * (1 << n)
    dp[0] = 0.0
    for eligible_bits, points in scored:
        for mask in range((1 << n) - 1, -1, -1):
            if dp[mask] == neg_inf:
                continue
            for b in eligible_bits:
                bit = 1 << b
                if mask & bit:
                    continue
                candidate = dp[mask] + points
                if candidate > dp[mask | bit]:
                    dp[mask | bit] = candidate

    # Fullest reachable lineup wins; total breaks ties.
    best_mask = max(
        (m for m in range(1 << n) if dp[m] > neg_inf),
        key=lambda m: (m.bit_count(), dp[m]),
    )
    return dp[best_mask]
