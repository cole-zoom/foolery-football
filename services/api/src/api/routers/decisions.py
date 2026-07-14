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
from decision_engine.core.lineup import assign_lineup
from decision_engine.core.pipeline import DecideRequest
from decision_engine.types import NflState, Player, ScoredCandidate, SnapshotData
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
from api.schemas import (
    CandidateOut,
    DecisionsOut,
    Pool,
    ScoreOut,
    SlotDecisionOut,
)

router = APIRouter(tags=["decisions"])

REGULAR_SEASON_LAST_WEEK = 18
# Effectively "return every scored candidate". The pipeline scores the
# whole eligible pool regardless of limit — limit only truncates the
# output — and we need more than the #1 pick here: the current
# starter's own score is what quantifies a SWAP for the UI.
CANDIDATE_SEARCH_LIMIT = 10_000


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
    model: str = Query(default="naive"),
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
    prefer_team: str | None = Query(default=None),
    avoid_team: str | None = Query(default=None),
) -> DecisionsOut:
    state = live_cache.get_state(http)
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
    league_context = live_cache.get_league_context(
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
    # A player's score is slot-independent, so share it across the slot
    # loop — with pool=waivers/both the WR/RB/TE/FLEX pools overlap by
    # thousands of players.
    score_cache: dict[str, ScoredCandidate] = {}
    # (slot_id, slot, current starter pid) per selectable slot; picks
    # are assigned after the loop, optimally over the whole lineup.
    slot_rows: list[tuple[str, str, str | None]] = []

    for i, slot in enumerate(league_context.league.roster_positions):
        seen[slot] += 1
        slot_id = f"{slot}{seen[slot]}"
        if slot in NON_SELECTABLE_SLOTS:
            continue
        slot_rows.append((slot_id, slot, starters[i] if i < len(starters) else None))

        request = DecideRequest(
            user=user,
            league_id=league_id,
            slot=slot,
            risk=risk,
            pool=cast(Pool, pool),
            limit=CANDIDATE_SEARCH_LIMIT,
            model=model,
            prefer_team=prefer,
            avoid_team=avoid,
            state_override=state_override,
        )

        result = decide_pipeline.run(
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

    # Optimal assignment over predicted points (PRD 3.3): one player per
    # slot, maximizing the summed final_score, instead of the greedy
    # in-league-order fill that burned e.g. the best QB in an early
    # SUPER_FLEX slot.
    assignment = assign_lineup([slot for _, slot, _ in slot_rows], score_cache)

    for slot_id, slot, current_pid in slot_rows:
        current_player = _player_lookup(snapshot, current_pid)
        picked_pid = assignment.get(slot_id)
        top_candidate = score_cache.get(picked_pid) if picked_pid else None
        if top_candidate is not None:
            sum_mean += top_candidate.score.projected_mean
            sum_variance += top_candidate.score.projected_variance ** 2

        current_candidate = (
            score_cache.get(current_pid) if current_pid is not None else None
        )

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
                current_starter_score=(
                    _to_score_out(current_candidate) if current_candidate else None
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
        score=_to_score_out(c),
    )


def _to_score_out(c) -> ScoreOut:
    return ScoreOut(
        projected_mean=c.score.projected_mean,
        projected_variance=c.score.projected_variance,
        risk_adjusted_score=c.score.risk_adjusted_score,
        final_score=c.final_score,
        confidence=c.score.confidence,
        notes=list(c.score.notes),
        preference_note=c.preference_note,
        on_user_roster=c.on_user_roster,
    )


def _default_season(live_state: LiveState) -> int:
    if live_state.week >= 2:
        return live_state.season
    return live_state.season - 1


def _default_week(season: int, live_state: LiveState) -> int:
    if season < live_state.season:
        return REGULAR_SEASON_LAST_WEEK
    return max(1, min(REGULAR_SEASON_LAST_WEEK, live_state.week - 1))
