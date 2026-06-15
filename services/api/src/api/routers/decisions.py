"""GET /leagues/{id}/decisions — top pick per starter slot in one call.

Drives the reactive lineup. Loads the league context once, runs the
decision pipeline per slot, sums the projection. Much faster than the
frontend firing N parallel /decide calls.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import cast

from decision_engine.core import pipeline as decide_pipeline
from decision_engine.core.eligibility import NON_SELECTABLE_SLOTS
from decision_engine.core.league_fetch import fetch_league_context, resolve_state
from decision_engine.core.pipeline import DecideRequest
from decision_engine.types import NflState, Player, SnapshotData
from fastapi import APIRouter, Query
from ffdm_app.types import LiveState

from api.deps import (
    HttpClientDep,
    PrepareSeasonDep,
    SettingsDep,
    SnapshotReaderDep,
)
from api.hydrate import player_to_wire
from api.schemas import (
    CandidateOut,
    DecisionsOut,
    Pool,
    ScoreOut,
    SlotDecisionOut,
)

router = APIRouter(tags=["decisions"])

REGULAR_SEASON_LAST_WEEK = 18


@router.get("/leagues/{league_id}/decisions", response_model=DecisionsOut)
def get_decisions(
    league_id: str,
    user: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    risk: float = Query(default=0.5, ge=0.0, le=1.0),
    pool: Pool = Query(default="roster"),
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
    prefer_team: str | None = Query(default=None),
    avoid_team: str | None = Query(default=None),
) -> DecisionsOut:
    state = resolve_state(http, override=None)
    live_state = LiveState(season=state.season, week=state.week)

    resolved_season = season if season is not None else _default_season(live_state)
    resolved_week = (
        week if week is not None else _default_week(resolved_season, live_state)
    )

    prepare_season(resolved_season, live_state)

    # Load once, reuse across every slot. Without this, we re-parse the
    # ~5MB snapshot 2N times (one in _player_lookup, one inside each
    # pipeline.run) and fire fetch_league_context N+1 times against
    # Sleeper. With the dep returning a process-wide caching reader,
    # repeated /decisions calls share the parse too.
    snapshot = snapshot_reader.load(resolved_season)
    league_context = fetch_league_context(
        http, username=user, league_id=league_id, season=resolved_season
    )

    base = settings.headshot_base_url
    seen: Counter[str] = Counter()
    decisions: list[SlotDecisionOut] = []
    sum_mean = 0.0
    sum_variance = 0.0

    starters = list(league_context.user_roster.starters)
    state_override = NflState(season=resolved_season, week=resolved_week)
    prefer = prefer_team.upper() if prefer_team else None
    avoid = avoid_team.upper() if avoid_team else None
    using_prior_season = False
    prior_season: int | None = None
    # A player recommended for one starter slot must not be recommended
    # for another (e.g. the best WR cannot fill WR1 AND FLEX). Track
    # picks across slots and exclude them from subsequent decisions.
    assigned_player_ids: set[str] = set()

    for i, slot in enumerate(league_context.league.roster_positions):
        seen[slot] += 1
        slot_id = f"{slot}{seen[slot]}"
        if slot in NON_SELECTABLE_SLOTS:
            continue

        current_pid = starters[i] if i < len(starters) else None
        current_player = _player_lookup(snapshot, current_pid)

        request = DecideRequest(
            user=user,
            league_id=league_id,
            slot=slot,
            risk=risk,
            pool=cast(Pool, pool),
            limit=1,
            model="naive",
            prefer_team=prefer,
            avoid_team=avoid,
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

        top_candidate = result.candidates[0] if result.candidates else None
        if top_candidate is not None:
            assigned_player_ids.add(top_candidate.player.player_id)
            sum_mean += top_candidate.score.projected_mean
            sum_variance += top_candidate.score.projected_variance ** 2

        decisions.append(
            SlotDecisionOut(
                slot_id=slot_id,
                slot=slot,
                recommended=_to_candidate_out(top_candidate, base) if top_candidate else None,
                current_starter=(
                    player_to_wire(current_player, headshot_base=base)
                    if current_player
                    else None
                ),
                matches_current=(
                    top_candidate is not None
                    and current_pid is not None
                    and top_candidate.player.player_id == current_pid
                ),
            )
        )

    return DecisionsOut(
        season=resolved_season,
        week=resolved_week,
        risk=risk,
        pool=cast(Pool, pool),
        decisions=decisions,
        projection_total=sum_mean,
        projection_variance_total=sum_variance,
        projection_stddev_total=math.sqrt(sum_variance),
        using_prior_season=using_prior_season,
        prior_season=prior_season,
    )


def _player_lookup(snapshot: SnapshotData, player_id: str | None) -> Player | None:
    if not player_id:
        return None
    return snapshot.players.get(player_id)


def _to_candidate_out(c, base: str) -> CandidateOut:
    return CandidateOut(
        rank=1,
        recommended=True,
        player=player_to_wire(c.player, headshot_base=base),
        score=ScoreOut(
            projected_mean=c.score.projected_mean,
            projected_variance=c.score.projected_variance,
            risk_adjusted_score=c.score.risk_adjusted_score,
            final_score=c.final_score,
            confidence=c.score.confidence,
            notes=list(c.score.notes),
            preference_note=c.preference_note,
            on_user_roster=c.on_user_roster,
        ),
    )


def _default_season(live_state: LiveState) -> int:
    if live_state.week >= 2:
        return live_state.season
    return live_state.season - 1


def _default_week(season: int, live_state: LiveState) -> int:
    if season < live_state.season:
        return REGULAR_SEASON_LAST_WEEK
    return max(1, min(REGULAR_SEASON_LAST_WEEK, live_state.week - 1))
