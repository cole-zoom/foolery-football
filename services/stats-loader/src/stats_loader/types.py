"""Pure data structures shared across layers.

Pydantic models for the *internal* shapes we hand around (Sleeper state,
manifest payload). Raw Sleeper responses are kept as plain ``dict`` — we
store them verbatim per PRD 1.1 / 1.2 and validate shape in the provider
layer.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Sleeper responses are JSON objects of arbitrary shape. We don't constrain
# the values, only that the top-level is a dict keyed by player_id.
SleeperPayload = dict[str, object]


class NflState(BaseModel):
    """Snapshot of `/v1/state/nfl` — what season + week is it right now."""

    season: int = Field(ge=1900)
    week: int = Field(ge=0)


class Manifest(BaseModel):
    """`manifest.json` payload. Committed last; doubles as the commit marker."""

    schema_version: int
    loader_version: str
    snapshot_started_at: datetime
    snapshot_finished_at: datetime
    season: int
    completed_through_week: int
    weeks_included: list[int]
    upcoming_week_projection: int | None
    prior_season_bootstrapped: bool
    sources: dict[str, str]
