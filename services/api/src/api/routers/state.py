"""GET /state — current NFL season + week per Sleeper."""

from __future__ import annotations

from fastapi import APIRouter

from api import live_cache
from api.deps import HttpClientDep
from api.schemas import StateOut

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateOut)
def get_state(http: HttpClientDep) -> StateOut:
    state = live_cache.get_state(http)
    return StateOut(season=state.season, week=state.week)
