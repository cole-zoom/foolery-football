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
