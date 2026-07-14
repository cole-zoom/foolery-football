"""GET /leagues/{id}/comparison — model hindsight vs the human's real lineup.

The replay itself (leakage-safe scoring, week-W matchup-archive roster
swap, slot-by-slot assembly, perfect-hindsight DP) lives in
``decision_engine.core.replay`` and is shared with the offline eval
harness. This router resolves live state, fetches the league context
and matchup archive, runs the replay, and decorates the result with
wire-format player rows plus per-player prediction accuracy.
"""

from __future__ import annotations

from decision_engine.core.league_fetch import (
    UserInputError,
    fetch_matchups,
)
from decision_engine.core.replay import replay_week_comparison
from decision_engine.core.scoring.common import weekly_points
from decision_engine.types import Pool
from fastapi import APIRouter, Query
from ffdm_app.types import LiveState

from api import live_cache
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


@router.get("/leagues/{league_id}/comparison", response_model=ComparisonOut)
def get_comparison(
    league_id: str,
    user: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    risk: float = Query(default=0.5, ge=0.0, le=1.0),
    pool: Pool = Query(default="roster"),
    model: str = Query(default="naive"),
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
) -> ComparisonOut:
    state = live_cache.get_state(http)
    live_state = LiveState(season=state.season, week=state.week)

    resolved_season = season if season is not None else _default_season(live_state)
    resolved_week = (
        week if week is not None else _default_week(resolved_season, live_state)
    )

    prepare_season(resolved_season, live_state)
    snapshot = snapshot_reader.load(resolved_season)

    # Fail before any league fetch — same check replay repeats, kept here
    # so an incomplete week never costs Sleeper round-trips.
    actual_table = snapshot.weekly_stats.get(resolved_week)
    if not actual_table:
        raise UserInputError(
            f"week {resolved_week} of {resolved_season} has no recorded stats yet — "
            "the comparison needs a completed week"
        )

    league_context = live_cache.get_league_context(
        http, username=user, league_id=league_id, season=resolved_season
    )
    scoring = league_context.league.scoring_settings

    matchups = fetch_matchups(http, league_id=league_id, week=resolved_week)
    if not any(
        m.roster_id == league_context.user_roster.roster_id for m in matchups
    ):
        raise UserInputError(
            f"no week-{resolved_week} matchup found for {user!r} in league "
            f"{league_id} — the league may not have played that week"
        )

    result = replay_week_comparison(
        http=http,
        snapshot_reader=snapshot_reader,
        snapshot=snapshot,
        league_context=league_context,
        matchups=matchups,
        season=resolved_season,
        week=resolved_week,
        model=model,
        risk=risk,
        pool=pool,
        candidate_limit=CANDIDATE_SEARCH_LIMIT,
    )

    base = settings.headshot_base_url
    predicted = result.predicted_mean

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

    slots_out = [
        ComparisonSlotOut(
            slot_id=pick.slot_id,
            slot=pick.slot,
            model_pick=row(pick.model_player_id),
            actual_starter=row(pick.human_player_id),
            same_player=(
                pick.model_player_id is not None
                and pick.human_player_id is not None
                and pick.model_player_id == pick.human_player_id
            ),
        )
        for pick in result.slot_picks
    ]

    week_roster = result.league_context.user_roster
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
        pool=pool,
        slots=slots_out,
        totals=ComparisonTotalsOut(
            model_predicted=result.model_predicted,
            model_actual=result.model_actual,
            human_predicted=result.human_predicted,
            human_actual=result.human_actual,
            perfect_actual=result.perfect_actual,
        ),
        accuracy=ComparisonAccuracyOut(
            n=len(errors),
            mae=sum(abs(e) for e in errors) / len(errors) if errors else None,
            mean_error=sum(errors) / len(errors) if errors else None,
        ),
        roster=roster_rows,
        using_prior_season=result.using_prior_season,
        prior_season=result.prior_season,
    )
