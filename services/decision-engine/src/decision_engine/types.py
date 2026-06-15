"""Pure data structures shared across layers.

Player, league, and score shapes the scoring model and CLI hand around.
Raw Sleeper responses live as ``dict`` in the provider layer; once they
pass shape validation they get lifted into these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["low", "medium", "high"]
Pool = Literal["roster", "waivers", "both"]

# Stat code -> point weight (e.g. ``{"pass_yd": 0.04, "rec": 1.0}``). The
# scoring model multiplies these against the same codes in the stats
# files. Anything not in the dict contributes zero points.
ScoringSettings = dict[str, float]


class Player(BaseModel):
    """Normalised view of a Sleeper player entry.

    The snapshot stores Sleeper's payload verbatim — we only lift the
    fields the scoring model and CLI actually use.
    """

    model_config = ConfigDict(frozen=True)

    player_id: str
    full_name: str | None = None
    position: str | None = None
    fantasy_positions: tuple[str, ...] = ()
    team: str | None = None
    status: str | None = None
    injury_status: str | None = None


class WeeklyStats(BaseModel):
    """One player's stat line for a single completed week."""

    model_config = ConfigDict(frozen=True)

    season: int
    week: int
    stats: dict[str, float]


class PlayerScore(BaseModel):
    """Scoring model output. Sorted desc by ``risk_adjusted_score``."""

    model_config = ConfigDict(frozen=True)

    player_id: str
    projected_mean: float
    projected_variance: float
    risk_adjusted_score: float
    confidence: Confidence
    notes: tuple[str, ...] = ()


class SleeperUser(BaseModel):
    """``/v1/user/<username>`` shape we care about."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str | None = None
    display_name: str | None = None


class LeagueSummary(BaseModel):
    """One entry from ``/v1/user/<user_id>/leagues/nfl/<season>``."""

    model_config = ConfigDict(frozen=True)

    league_id: str
    name: str
    season: str


class League(BaseModel):
    """Full league response with the fields scoring + eligibility need."""

    model_config = ConfigDict(frozen=True)

    league_id: str
    name: str
    season: str
    roster_positions: tuple[str, ...]
    scoring_settings: ScoringSettings


class Roster(BaseModel):
    """One ``/v1/league/<id>/rosters`` entry."""

    model_config = ConfigDict(frozen=True)

    roster_id: int
    owner_id: str | None
    players: tuple[str, ...] = ()
    starters: tuple[str, ...] = ()


class LeagueContext(BaseModel):
    """Everything pipeline needs from the live Sleeper league fetch."""

    model_config = ConfigDict(frozen=True)

    user: SleeperUser
    league: League
    rosters: tuple[Roster, ...]
    user_roster: Roster

    @property
    def all_rostered_player_ids(self) -> frozenset[str]:
        ids: set[str] = set()
        for r in self.rosters:
            ids.update(r.players)
        return frozenset(ids)


class SnapshotData(BaseModel):
    """In-memory view of the latest ``data/snapshots/<date>/`` folder."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    snapshot_dir: str
    schema_version: int
    season: int
    weeks_included: tuple[int, ...]
    upcoming_week_projection: int | None
    players: dict[str, Player]
    weekly_stats: dict[int, dict[str, dict[str, float]]] = Field(default_factory=dict)
    prior_season_stats: dict[str, dict[str, float]] = Field(default_factory=dict)


class NflState(BaseModel):
    """``/v1/state/nfl`` — what season + week is it right now."""

    model_config = ConfigDict(frozen=True)

    season: int = Field(ge=1900)
    week: int = Field(ge=0)


class ScoredCandidate(BaseModel):
    """A scored player plus presentation-layer adjustments.

    The pipeline applies team-preference multipliers *after* the scoring
    model returns. We keep both numbers — base and adjusted — so the CLI
    can attribute the change in the notes column.
    """

    model_config = ConfigDict(frozen=True)

    player: Player
    score: PlayerScore
    final_score: float
    preference_note: str | None = None
    on_user_roster: bool = False
