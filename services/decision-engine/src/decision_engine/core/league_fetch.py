"""Live Sleeper league fetch — PRD 2.1.

Pure orchestration of the http client; never constructs one. The
result is a typed ``LeagueContext`` the pipeline consumes.

Failure modes raise either ``UserInputError`` (exit 1 at CLI) or
``HttpError``/``SchemaError`` (exit 2).
"""

from __future__ import annotations

import logging

from decision_engine.clients.http import HttpClient, NotFoundError
from decision_engine.providers import sleeper
from decision_engine.types import LeagueContext, LeagueSummary, NflState, Roster

log = logging.getLogger(__name__)


class UserInputError(ValueError):
    """User-facing input mistake (unknown user, league mismatch, etc.).

    Surfaces as exit code 1 at the CLI.
    """


def resolve_state(http: HttpClient, override: NflState | None) -> NflState:
    if override is not None:
        log.info("Using state override: season=%d week=%d", override.season, override.week)
        return override
    return sleeper.validate_state(http.get_json("/v1/state/nfl"))


def fetch_league_context(
    http: HttpClient,
    *,
    username: str,
    league_id: str,
    season: int,
) -> LeagueContext:
    """Run the full PRD 2.1 resolution flow against Sleeper."""

    # 1. username -> user_id
    try:
        user_payload = http.get_json(f"/v1/user/{username}")
    except NotFoundError as exc:
        raise UserInputError(
            f"unknown Sleeper username {username!r}"
        ) from exc
    user = sleeper.validate_user(user_payload)
    log.info("Resolved %s -> user_id=%s", username, user.user_id)

    # 2. user's leagues for the season -> validate the requested league_id
    leagues_payload = http.get_json(
        f"/v1/user/{user.user_id}/leagues/nfl/{season}"
    )
    leagues = sleeper.validate_user_leagues(leagues_payload)
    if not any(lg.league_id == league_id for lg in leagues):
        raise UserInputError(_league_mismatch_message(username, season, league_id, leagues))

    # 3. league settings
    league_payload = http.get_json(f"/v1/league/{league_id}")
    league = sleeper.validate_league(league_payload)

    # 4. rosters -> find the user's
    rosters_payload = http.get_json(f"/v1/league/{league_id}/rosters")
    rosters = sleeper.validate_rosters(rosters_payload)
    user_roster = _find_user_roster(rosters, user.user_id)
    if user_roster is None:
        # This should never happen if /leagues returned the league. PRD
        # 2.1 calls this a bug; surface as a non-input error.
        raise RuntimeError(
            f"user_id={user.user_id} not found in any roster on league {league_id}; "
            "Sleeper returned the league but no matching roster — this is a bug."
        )

    return LeagueContext(
        user=user,
        league=league,
        rosters=tuple(rosters),
        user_roster=user_roster,
    )


def _find_user_roster(rosters: list[Roster], user_id: str) -> Roster | None:
    for r in rosters:
        if r.owner_id == user_id:
            return r
    return None


def _league_mismatch_message(
    username: str,
    season: int,
    league_id: str,
    leagues: list[LeagueSummary],
) -> str:
    if not leagues:
        return (
            f"user {username!r} has no NFL leagues for season {season}; "
            f"--league {league_id} cannot match."
        )
    listed = "\n".join(f"  - {lg.league_id}  {lg.name}" for lg in leagues)
    return (
        f"--league {league_id} is not one of {username!r}'s {season} leagues.\n"
        f"Available leagues:\n{listed}"
    )
