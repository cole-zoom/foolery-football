"""Shape validation for Sleeper API responses.

Pure functions over already-decoded JSON. No HTTP, no filesystem.

The loader stores Sleeper payloads verbatim (PRD 1.1, 1.2). The
provider's job is to make sure the payload is *plausibly* what we
expect, so we never write a "successful" but useless snapshot.

Failure modes here all raise ``SchemaError`` — the pipeline catches it
and aborts the whole run without writing anything to disk.
"""

from __future__ import annotations

import logging
from typing import Final

from stats_loader.types import NflState, SleeperPayload

log = logging.getLogger(__name__)

# Per PRD 1.1: the real /v1/players/nfl response is ~10k entries. A
# response with fewer than this many entries is almost certainly a
# partial response or upstream change.
MIN_PLAYER_COUNT: Final[int] = 1000

# Per PRD 1.1: these are the fields the decision engine actually reads.
# If ALL entries are missing one, abort with a schema-change error.
# (Individual entries can miss `position` — defenses, retired players —
# and are kept; the engine filters.)
LOAD_BEARING_PLAYER_FIELDS: Final[tuple[str, ...]] = (
    "player_id",
    "full_name",
    "position",
    "fantasy_positions",
    "team",
    "status",
    "injury_status",
)


class SchemaError(ValueError):
    """Sleeper returned something we don't recognise. Abort the run."""


def validate_state(payload: object) -> NflState:
    """Validate `/v1/state/nfl`. Returns a typed NflState."""

    if not isinstance(payload, dict):
        raise SchemaError(f"/v1/state/nfl: expected object, got {type(payload).__name__}")
    try:
        return NflState.model_validate(payload)
    except Exception as exc:
        raise SchemaError(f"/v1/state/nfl: malformed: {exc}") from exc


def validate_players(payload: object) -> SleeperPayload:
    """Validate `/v1/players/nfl`. Returns the payload unchanged on success.

    Stored verbatim per PRD 1.1 — we don't transform, just gatekeep.
    """

    if not isinstance(payload, dict):
        raise SchemaError(f"/v1/players/nfl: expected object, got {type(payload).__name__}")

    if len(payload) < MIN_PLAYER_COUNT:
        raise SchemaError(
            "Sleeper returned too few players "
            f"({len(payload)} < {MIN_PLAYER_COUNT}); "
            "possible partial response or schema change."
        )

    # Each entry's `player_id` field must be present and string-typed.
    # Entries missing other fields are logged-and-kept (quarantine over
    # drop — but for player_id specifically, the join key, we abort).
    for pid, entry in payload.items():
        if not isinstance(entry, dict):
            raise SchemaError(f"/v1/players/nfl: entry {pid!r} is not an object")
        inner_pid = entry.get("player_id")
        if not isinstance(inner_pid, str):
            raise SchemaError(
                f"/v1/players/nfl: entry {pid!r} missing string player_id"
            )

    # If a load-bearing field is missing from *every* entry, that's a
    # schema change we want to fail loud on. We exempt `player_id`
    # because we already enforced it above.
    for field in LOAD_BEARING_PLAYER_FIELDS:
        if field == "player_id":
            continue
        if not any(field in entry for entry in payload.values() if isinstance(entry, dict)):
            raise SchemaError(
                f"/v1/players/nfl: load-bearing field {field!r} missing from every entry — "
                "did Sleeper change the schema?"
            )

    return payload


def validate_weekly(payload: object, *, label: str, allow_empty: bool) -> SleeperPayload:
    """Validate a `/v1/stats/...` or `/v1/projections/...` response.

    Shape: object keyed by player_id, values are flat stat-code maps.

    ``allow_empty`` is True only for the current in-progress week's
    projections — past weeks coming back empty signals an upstream change
    and we abort (PRD 1.2).
    """

    if not isinstance(payload, dict):
        raise SchemaError(f"{label}: expected object, got {type(payload).__name__}")

    if not payload and not allow_empty:
        raise SchemaError(
            f"{label}: empty response for a completed week — "
            "possible upstream change, aborting."
        )

    # Per-record validation: malformed individual entries get logged and
    # skipped, never absorbed silently (quarantine over drop). We don't
    # mutate the payload — verbatim storage — but we do warn so the run
    # log shows what's off.
    for pid, entry in payload.items():
        if not isinstance(entry, dict):
            log.warning(
                "%s: player %r value is not an object (%s); kept verbatim",
                label,
                pid,
                type(entry).__name__,
            )

    return payload
