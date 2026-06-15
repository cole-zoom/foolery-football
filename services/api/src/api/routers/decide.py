"""GET /decide — the recommendation endpoint.

Wraps ``ffdm_app.session.decide`` directly so the API output matches the
CLI output. The session layer handles snapshot freshness + the live
Sleeper fetch; we just translate query params and shape the response.
"""

from __future__ import annotations

from typing import cast

from decision_engine.core.league_fetch import resolve_state
from fastapi import APIRouter, Query
from ffdm_app import session as app_session
from ffdm_app.types import AppRequest, LiveState

from api.deps import (
    HttpClientDep,
    PrepareSeasonDep,
    SettingsDep,
    SnapshotReaderDep,
)
from api.hydrate import player_to_wire
from api.schemas import CandidateOut, DecideOut, Pool, ScoreOut

router = APIRouter(tags=["decide"])


@router.get("/decide", response_model=DecideOut)
def decide(
    user: str,
    league_id: str,
    slot: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    risk: float = Query(default=0.5, ge=0.0, le=1.0),
    pool: Pool = Query(default="roster"),
    limit: int = Query(default=20, ge=1, le=200),
    model: str = Query(default="naive"),
    season: int | None = Query(default=None),
    week: int | None = Query(default=None),
    prefer_team: str | None = Query(default=None),
    avoid_team: str | None = Query(default=None),
) -> DecideOut:
    state = resolve_state(http, override=None)
    live_state = LiveState(season=state.season, week=state.week)

    resolved_season = season if season is not None else app_session.default_season(live_state)
    resolved_week = (
        week
        if week is not None
        else app_session.default_week_for_season(resolved_season, live_state=live_state)
    )

    request = AppRequest(
        league_id=league_id,
        user=user,
        season=resolved_season,
        week=resolved_week,
        slot=slot.upper(),
        risk=risk,
        pool=pool,
        limit=limit,
        model=model,
        prefer_team=prefer_team.upper() if prefer_team else None,
        avoid_team=avoid_team.upper() if avoid_team else None,
        snapshot_root=settings.snapshot_root,
        sleeper_base_url=settings.sleeper_base_url,
    )

    result = app_session.decide(
        request,
        live_state=live_state,
        snapshot_reader=snapshot_reader,
        prepare_season=prepare_season,
    )

    base = settings.headshot_base_url
    candidates = [
        CandidateOut(
            rank=i + 1,
            recommended=(i == 0),
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
        for i, c in enumerate(result.candidates)
    ]

    return DecideOut(
        season=result.state.season,
        week=result.state.week,
        slot=slot.upper(),
        pool=cast(Pool, pool),
        risk=risk,
        candidates=candidates,
    )
