"""Wire-level Pydantic models. These are what the browser sees.

Keep these stable — the React app types against them. Domain models
(Player, ScoredCandidate, etc.) live in decision_engine.types and may
change independently.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]
Pool = Literal["roster", "waivers", "both"]
# Availability-gate source (decision_engine.types.AvailabilityMode):
# who counts as startable. "sleeper" is the default everywhere; the
# other modes power the UI's injury-gate knob.
Availability = Literal["sleeper", "heuristic", "news", "none"]


class StateOut(BaseModel):
    season: int
    week: int


class LeagueSummaryOut(BaseModel):
    league_id: str
    name: str
    season: str


class UserLeaguesOut(BaseModel):
    user_id: str
    username: str | None
    display_name: str | None
    leagues: list[LeagueSummaryOut]


class PlayerOut(BaseModel):
    player_id: str
    full_name: str | None
    position: str | None
    fantasy_positions: list[str]
    team: str | None
    status: str | None
    injury_status: str | None
    headshot_url: str | None


class RosterSlotOut(BaseModel):
    """One position slot in the user's lineup.

    Multiple BN / FLEX slots get indexed (BN1, BN2, ...) so the React
    grid can give each one a stable key.
    """

    slot_id: str
    slot: str
    selectable: bool
    starter_player: PlayerOut | None


class LeagueContextOut(BaseModel):
    league: LeagueSummaryOut
    user_id: str
    username: str | None
    display_name: str | None
    roster_positions: list[str]
    slots: list[RosterSlotOut]
    bench: list[PlayerOut]
    all_roster_players: list[PlayerOut]


class ScoreOut(BaseModel):
    projected_mean: float
    projected_variance: float
    risk_adjusted_score: float
    final_score: float
    confidence: Confidence
    notes: list[str]
    preference_note: str | None
    on_user_roster: bool


class CandidateOut(BaseModel):
    rank: int
    recommended: bool = Field(
        description="True for the #1 ranked candidate — the recommended pick."
    )
    player: PlayerOut
    score: ScoreOut


class DecideOut(BaseModel):
    season: int
    week: int
    slot: str
    pool: Pool
    risk: float
    candidates: list[CandidateOut]


class SlotDecisionOut(BaseModel):
    """One slot's top recommendation plus whether it matches the current starter."""

    slot_id: str
    slot: str
    recommended: CandidateOut | None
    current_starter: PlayerOut | None
    matches_current: bool
    # The current starter's own score under the same model/risk/settings.
    # Lets the UI quantify a SWAP: "starting X over Y projects +N pts".
    # None when there's no current starter or they weren't scoreable
    # (e.g. already recommended into an earlier slot).
    current_starter_score: ScoreOut | None = None


class DecisionsOut(BaseModel):
    """Top recommendation across every selectable starter slot."""

    season: int
    week: int
    risk: float
    pool: Pool
    decisions: list[SlotDecisionOut]
    projection_total: float
    projection_variance_total: float
    projection_stddev_total: float
    using_prior_season: bool = False
    prior_season: int | None = None


class ComparisonPlayerOut(BaseModel):
    """One player's predicted-vs-actual line for a completed week.

    ``predicted_mean`` is None when the model never scored the player
    (no starter slot accepts their position). ``actual_points`` is None
    when they produced no stat row that week — i.e. they didn't play.
    """

    player: PlayerOut
    predicted_mean: float | None
    actual_points: float | None


class ComparisonSlotOut(BaseModel):
    """Model's replayed pick vs the starter the human actually fielded."""

    slot_id: str
    slot: str
    model_pick: ComparisonPlayerOut | None
    actual_starter: ComparisonPlayerOut | None
    same_player: bool


class ComparisonTotalsOut(BaseModel):
    """Lineup totals, all measured with the league's own scoring math.

    ``human_predicted`` is None when none of the actual starters were
    scoreable. ``perfect_actual`` is the best-possible total from that
    week's roster with hindsight (None if it couldn't be computed).
    """

    model_predicted: float
    model_actual: float
    human_predicted: float | None
    human_actual: float
    perfect_actual: float | None


class ComparisonAccuracyOut(BaseModel):
    """Prediction-quality stats over roster players who played.

    ``mean_error`` is signed (predicted - actual): positive means the
    model over-predicted on average.
    """

    n: int
    mae: float | None
    mean_error: float | None


class ComparisonOut(BaseModel):
    """Model-vs-human retrospective for one completed week."""

    season: int
    week: int
    model: str
    risk: float
    pool: Pool
    slots: list[ComparisonSlotOut]
    totals: ComparisonTotalsOut
    accuracy: ComparisonAccuracyOut
    roster: list[ComparisonPlayerOut]
    using_prior_season: bool = False
    prior_season: int | None = None


class WeeklyStatLineOut(BaseModel):
    week: int
    points: float
    stats: dict[str, float]


class PlayerStatsOut(BaseModel):
    player: PlayerOut
    season: int
    weeks: list[WeeklyStatLineOut]
    season_total_points: float
    games_played: int
    points_per_game: float
    mean: float
    stddev: float
    using_prior_season: bool = False
    prior_season: int | None = None
