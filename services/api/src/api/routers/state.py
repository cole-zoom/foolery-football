"""GET /state — current NFL season + week per Sleeper."""

from __future__ import annotations

from decision_engine.core.league_fetch import resolve_state
from fastapi import APIRouter

from api.deps import HttpClientDep
from api.schemas import StateOut

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateOut)
def get_state(http: HttpClientDep) -> StateOut:
    state = resolve_state(http, override=None)
    return StateOut(season=state.season, week=state.week)
