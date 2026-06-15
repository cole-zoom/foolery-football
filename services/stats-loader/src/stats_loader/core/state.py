"""Derive what weeks to fetch from the current NFL state.

Pure functions. PRD 1.2 §"What we fetch on each run".
"""

from __future__ import annotations

from dataclasses import dataclass

from stats_loader.types import NflState


@dataclass(frozen=True, slots=True)
class FetchPlan:
    """The set of artifacts a single run needs to fetch."""

    season: int
    completed_through_week: int
    completed_weeks: tuple[int, ...]
    upcoming_week: int | None
    bootstrap_prior_season: bool

    @property
    def prior_season(self) -> int:
        return self.season - 1


def plan_from_state(state: NflState) -> FetchPlan:
    """Compute the fetch plan from `/v1/state/nfl`.

    Rules (PRD 1.2):
    - ``completed_through = state.week - 1`` (most recently completed week).
    - Fetch stats + projections for each ``w in 1..completed_through``.
    - Fetch ``projections_week_<state.week>`` (upcoming / in-progress).
    - If ``completed_through == 0``, bootstrap from prior season.

    ``state.week == 0`` means we're pre-season-start; treat as if no
    weeks are completed and no upcoming-week projection is available
    (Sleeper has nothing to project for "week 0").
    """

    completed_through = max(0, state.week - 1)
    completed_weeks = tuple(range(1, completed_through + 1))
    upcoming_week = state.week if state.week >= 1 else None
    bootstrap = completed_through == 0

    return FetchPlan(
        season=state.season,
        completed_through_week=completed_through,
        completed_weeks=completed_weeks,
        upcoming_week=upcoming_week,
        bootstrap_prior_season=bootstrap,
    )
