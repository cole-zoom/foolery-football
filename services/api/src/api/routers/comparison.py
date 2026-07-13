"""GET /leagues/{id}/comparison — model hindsight vs the human's real lineup.

For a completed week W this replays the model's recommended lineup under
the same leakage-safe contract as /decisions (scoring sees only weeks
before W), pulls the lineup the manager *actually* fielded from
Sleeper's matchup archive, and scores both against what really happened
using the league's own scoring weights. Also reports per-player
prediction accuracy (predicted mean vs actual points) for the roster.

Historical fidelity: the candidate pool and starters both come from the
week-W matchup entry, not from /rosters — Sleeper's roster endpoint only
returns *current* state, so mid-season trades and pickups would
otherwise leak into the replay.
"""

from __future__ import annotations

from collections import Counter
from typing import cast

from decision_engine.core import pipeline as decide_pipeline
from decision_engine.core.eligibility import (
    NON_SELECTABLE_SLOTS,
    player_eligible_for_slot,
)
from decision_engine.core.league_fetch import (
    UserInputError,
    fetch_league_context,
    fetch_matchups,
    resolve_state,
)
from decision_engine.core.pipeline import DecideRequest
from decision_engine.core.scoring.common import weekly_points
from decision_engine.types import NflState, Pool, ScoringSettings, SnapshotData
from fastapi import APIRouter, Query
from ffdm_app.types import LiveState

from api.deps import (
    HttpClientDep,
    PrepareSeasonDep,
    SettingsDep,
    SnapshotReaderDep,
)
from api.hydrate import player_to_wire
from api.routers.decisions import (
    CANDIDATE_SEARCH_LIMIT,
    _default_season,
    _default_week,
)
from api.schemas import (
    ComparisonAccuracyOut,
    ComparisonOut,
    ComparisonPlayerOut,
    ComparisonSlotOut,
    ComparisonTotalsOut,
)

router = APIRouter(tags=["comparison"])

# Above this many starter slots the exact perfect-lineup search (bitmask
# DP over slots) stops being cheap; no real league gets close.
PERFECT_LINEUP_MAX_SLOTS = 14


@router.get("/leagues/{league_id}/comparison", response_model=ComparisonOut)
def get_comparison(
    league_id: str,
    user: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    risk: float = Query(default=0.5, ge=0.0, le=1.0),
    model: str = Query(default="naive"),
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
) -> ComparisonOut:
    state = resolve_state(http, override=None)
    live_state = LiveState(season=state.season, week=state.week)

    resolved_season = season if season is not None else _default_season(live_state)
    resolved_week = (
        week if week is not None else _default_week(resolved_season, live_state)
    )

    prepare_season(resolved_season, live_state)
    snapshot = snapshot_reader.load(resolved_season)

    actual_table = snapshot.weekly_stats.get(resolved_week)
    if not actual_table:
        raise UserInputError(
            f"week {resolved_week} of {resolved_season} has no recorded stats yet — "
            "the comparison needs a completed week"
        )

    league_context = fetch_league_context(
        http, username=user, league_id=league_id, season=resolved_season
    )
    scoring = league_context.league.scoring_settings

    matchups = fetch_matchups(http, league_id=league_id, week=resolved_week)
    matchup = next(
        (m for m in matchups if m.roster_id == league_context.user_roster.roster_id),
        None,
    )
    if matchup is None:
        raise UserInputError(
            f"no week-{resolved_week} matchup found for {user!r} in league "
            f"{league_id} — the league may not have played that week"
        )

    # Swap in the week-W roster/starters so both the model's pool and the
    # "human" baseline are what actually existed that week. Empty fields
    # (very old leagues) fall back to the live roster rather than abort.
    week_roster = league_context.user_roster.model_copy(
        update={
            "players": matchup.players or league_context.user_roster.players,
            "starters": matchup.starters or league_context.user_roster.starters,
        }
    )
    league_context = league_context.model_copy(update={"user_roster": week_roster})

    base = settings.headshot_base_url
    state_override = NflState(season=resolved_season, week=resolved_week)

    # projected_mean per player_id, pooled across every slot run — a
    # player's mean is slot-independent, so first sighting wins. Fills in
    # predictions for actual starters even when a slot run excluded them
    # (already assigned to an earlier slot).
    predicted: dict[str, float] = {}

    def row(player_id: str | None) -> ComparisonPlayerOut | None:
        if not player_id:
            return None
        player = snapshot.players.get(player_id)
        if player is None:
            return None
        stats = actual_table.get(player_id)
        return ComparisonPlayerOut(
            player=player_to_wire(player, headshot_base=base),
            predicted_mean=predicted.get(player_id),
            actual_points=weekly_points(stats, scoring) if stats else None,
        )

    starters = list(week_roster.starters)
    seen: Counter[str] = Counter()
    assigned_player_ids: set[str] = set()
    slot_picks: list[tuple[str, str, str | None, str | None]] = []
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
            user=user,
            league_id=league_id,
            slot=slot,
            risk=risk,
            pool=cast(Pool, "roster"),
            limit=CANDIDATE_SEARCH_LIMIT,
            model=model,
            prefer_team=None,
            avoid_team=None,
            state_override=state_override,
            exclude_player_ids=frozenset(assigned_player_ids),
        )
        result = decide_pipeline.run(
            http=http,
            snapshot_reader=snapshot_reader,
            request=request,
            snapshot=snapshot,
            league_context=league_context,
        )
        if result.using_prior_season:
            using_prior_season = True
            prior_season = result.prior_season

        for c in result.candidates:
            predicted.setdefault(c.player.player_id, c.score.projected_mean)

        top = result.candidates[0] if result.candidates else None
        if top is not None:
            assigned_player_ids.add(top.player.player_id)

        starter_pid = starters[i] if i < len(starters) else None
        slot_picks.append(
            (
                slot_id,
                slot,
                top.player.player_id if top else None,
                starter_pid,
            )
        )

    # Rows are built after the loop so every player carries the pooled
    # prediction, not just what the model had seen by their slot's turn.
    slots_out: list[ComparisonSlotOut] = []
    model_predicted = 0.0
    model_actual = 0.0
    human_predicted_parts: list[float] = []
    human_actual = 0.0
    for slot_id, slot, pick_pid, starter_pid in slot_picks:
        model_pick = row(pick_pid)
        actual_starter = row(starter_pid)
        if model_pick is not None:
            model_predicted += model_pick.predicted_mean or 0.0
            model_actual += model_pick.actual_points or 0.0
        if actual_starter is not None:
            if actual_starter.predicted_mean is not None:
                human_predicted_parts.append(actual_starter.predicted_mean)
            human_actual += actual_starter.actual_points or 0.0
        slots_out.append(
            ComparisonSlotOut(
                slot_id=slot_id,
                slot=slot,
                model_pick=model_pick,
                actual_starter=actual_starter,
                same_player=(
                    pick_pid is not None
                    and starter_pid is not None
                    and pick_pid == starter_pid
                ),
            )
        )

    roster_rows = [r for pid in week_roster.players if (r := row(pid)) is not None]
    errors = [
        r.predicted_mean - r.actual_points
        for r in roster_rows
        if r.predicted_mean is not None and r.actual_points is not None
    ]

    return ComparisonOut(
        season=resolved_season,
        week=resolved_week,
        model=model,
        risk=risk,
        slots=slots_out,
        totals=ComparisonTotalsOut(
            model_predicted=model_predicted,
            model_actual=model_actual,
            human_predicted=(
                sum(human_predicted_parts) if human_predicted_parts else None
            ),
            human_actual=human_actual,
            perfect_actual=_perfect_lineup_total(
                selectable_slots,
                week_roster.players,
                snapshot,
                actual_table,
                scoring,
            ),
        ),
        accuracy=ComparisonAccuracyOut(
            n=len(errors),
            mae=sum(abs(e) for e in errors) / len(errors) if errors else None,
            mean_error=sum(errors) / len(errors) if errors else None,
        ),
        roster=roster_rows,
        using_prior_season=using_prior_season,
        prior_season=prior_season,
    )


def _perfect_lineup_total(
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
