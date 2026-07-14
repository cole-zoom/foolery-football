"""Pydantic / dataclass types for the app layer.

These are the inputs a UI (CLI today, web later) hands to ``session``.
Kept here so the future web layer can import them without depending on
``cli.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppRequest:
    """Everything ``session.decide`` needs for one ranked recommendation.

    ``league_id`` and ``user`` are identifiers Sleeper resolves live.
    ``season`` selects which on-disk cache to read.
    ``week`` is the *replay target*: scoring uses stats through week-1
    and projection for ``week``. When ``week`` equals the latest
    completed week of the season, this matches the live "this week's
    pick" question.
    """

    league_id: str
    user: str
    season: int
    week: int
    slot: str
    risk: float = 0.5
    pool: str = "roster"
    limit: int = 10
    model: str = "naive"
    # Availability-gate source (decision_engine.types.AvailabilityMode).
    availability: str = "sleeper"
    prefer_team: str | None = None
    avoid_team: str | None = None
    snapshot_root: Path | None = None
    sleeper_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class SeasonInfo:
    """A season we can offer in the picker.

    ``cached`` means ``data/seasons/<year>/`` exists on disk now. If
    False, ``ensure_season(year)`` will download it on demand.
    """

    season: int
    cached: bool
    completed_through_week: int | None  # None if not cached / unknown


@dataclass(frozen=True, slots=True)
class LiveState:
    """Result of ``/v1/state/nfl`` — the current season + week per Sleeper."""

    season: int
    week: int
