"""Shape validation for Sleeper API responses used by the decision engine.

Pure functions over decoded JSON. No HTTP, no filesystem.

These exist because the live league fetch is fragile — Sleeper changes
response shapes occasionally and the official docs omit several
endpoints (see ``docs/references/sleeper-api.md``). We fail loud and
early when something's off, before the scoring model gets confused by
a missing key.
"""

from __future__ import annotations

import logging

from decision_engine.types import (
    League,
    LeagueSummary,
    NflState,
    Roster,
    SleeperUser,
)

log = logging.getLogger(__name__)


class SchemaError(ValueError):
    """Sleeper returned something we don't recognise. Abort the run."""


def validate_state(payload: object) -> NflState:
    """Validate ``/v1/state/nfl``."""

    if not isinstance(payload, dict):
        raise SchemaError(f"/v1/state/nfl: expected object, got {type(payload).__name__}")
    try:
        return NflState.model_validate(payload)
    except Exception as exc:
        raise SchemaError(f"/v1/state/nfl: malformed: {exc}") from exc


def validate_user(payload: object) -> SleeperUser:
    """Validate ``/v1/user/<username>``."""

    if not isinstance(payload, dict):
        raise SchemaError(f"/v1/user: expected object, got {type(payload).__name__}")
    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise SchemaError("/v1/user: missing string user_id")
    return SleeperUser(
        user_id=user_id,
        username=_opt_str(payload.get("username")),
        display_name=_opt_str(payload.get("display_name")),
    )


def validate_user_leagues(payload: object) -> list[LeagueSummary]:
    """Validate ``/v1/user/<user_id>/leagues/nfl/<season>``."""

    if not isinstance(payload, list):
        raise SchemaError(
            f"/v1/user/.../leagues: expected list, got {type(payload).__name__}"
        )
    out: list[LeagueSummary] = []
    for i, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.warning("user leagues entry %d not an object; skipping", i)
            continue
        league_id = entry.get("league_id")
        name = entry.get("name") or "(unnamed)"
        season = entry.get("season") or ""
        if not isinstance(league_id, str) or not league_id:
            log.warning("user leagues entry %d missing string league_id; skipping", i)
            continue
        out.append(
            LeagueSummary(
                league_id=league_id,
                name=str(name),
                season=str(season),
            )
        )
    return out


def validate_league(payload: object) -> League:
    """Validate ``/v1/league/<league_id>``."""

    if not isinstance(payload, dict):
        raise SchemaError(f"/v1/league: expected object, got {type(payload).__name__}")
    league_id = payload.get("league_id")
    if not isinstance(league_id, str) or not league_id:
        raise SchemaError("/v1/league: missing string league_id")

    roster_positions = payload.get("roster_positions")
    if not isinstance(roster_positions, list) or not roster_positions:
        raise SchemaError("/v1/league: missing or empty roster_positions")
    roster_positions_tuple = tuple(str(p) for p in roster_positions)

    scoring_settings = payload.get("scoring_settings")
    if not isinstance(scoring_settings, dict) or not scoring_settings:
        raise SchemaError("/v1/league: missing or empty scoring_settings")
    # Sleeper sometimes ships ints; coerce to float and drop anything
    # non-numeric (quarantine over drop).
    scoring: dict[str, float] = {}
    for k, v in scoring_settings.items():
        if not isinstance(k, str):
            log.warning("league scoring_settings key %r non-string; skipping", k)
            continue
        if isinstance(v, bool) or not isinstance(v, int | float):
            log.warning("league scoring_settings[%s] = %r non-numeric; skipping", k, v)
            continue
        scoring[k] = float(v)

    return League(
        league_id=league_id,
        name=str(payload.get("name") or "(unnamed)"),
        season=str(payload.get("season") or ""),
        roster_positions=roster_positions_tuple,
        scoring_settings=scoring,
    )


def validate_rosters(payload: object) -> list[Roster]:
    """Validate ``/v1/league/<id>/rosters``."""

    if not isinstance(payload, list):
        raise SchemaError(
            f"/v1/league/rosters: expected list, got {type(payload).__name__}"
        )
    out: list[Roster] = []
    for i, entry in enumerate(payload):
        if not isinstance(entry, dict):
            log.warning("rosters entry %d not an object; skipping", i)
            continue
        roster_id = entry.get("roster_id")
        if not isinstance(roster_id, int):
            log.warning("rosters entry %d missing int roster_id; skipping", i)
            continue
        owner_id = entry.get("owner_id")
        players = entry.get("players") or ()
        starters = entry.get("starters") or ()
        out.append(
            Roster(
                roster_id=roster_id,
                owner_id=str(owner_id) if isinstance(owner_id, str) else None,
                players=tuple(str(p) for p in players if isinstance(p, str)),
                starters=tuple(str(p) for p in starters if isinstance(p, str)),
            )
        )
    return out


def _opt_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
