"""GET /users/{username}/leagues — list a Sleeper user's leagues."""

from __future__ import annotations

from decision_engine.clients.http import NotFoundError
from decision_engine.core.league_fetch import UserInputError, resolve_state
from decision_engine.providers import sleeper
from fastapi import APIRouter, Query

from api.deps import HttpClientDep
from api.schemas import LeagueSummaryOut, UserLeaguesOut

router = APIRouter(tags=["leagues"])


@router.get("/users/{username}/leagues", response_model=UserLeaguesOut)
def list_user_leagues(
    username: str,
    http: HttpClientDep,
    season: int | None = Query(
        default=None,
        description="NFL season. Defaults to the current season from /v1/state/nfl.",
    ),
) -> UserLeaguesOut:
    try:
        user_payload = http.get_json(f"/v1/user/{username}")
    except NotFoundError as exc:
        raise UserInputError(f"unknown Sleeper username {username!r}") from exc
    # Sleeper returns 200 + null body for unknown usernames. Translate
    # to a 400 so the frontend can show a clean "user not found" toast
    # instead of a 502 schema error.
    if user_payload is None:
        raise UserInputError(f"unknown Sleeper username {username!r}")
    user = sleeper.validate_user(user_payload)

    resolved_season = season if season is not None else resolve_state(http, None).season

    leagues_payload = http.get_json(
        f"/v1/user/{user.user_id}/leagues/nfl/{resolved_season}"
    )
    leagues = sleeper.validate_user_leagues(leagues_payload)

    return UserLeaguesOut(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        leagues=[
            LeagueSummaryOut(
                league_id=lg.league_id, name=lg.name, season=lg.season
            )
            for lg in leagues
        ],
    )
