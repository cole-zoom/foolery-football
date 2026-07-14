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
# How the pipeline decides a player is available to start in week W:
# "sleeper" — gate on Sleeper's pre-kickoff projection entry (PRD 3.1);
# "heuristic" — gate on our own archive: played in his team's most
# recent completed game (milestone 4's fully sleeper-free mode);
# "none" — no availability filter (bye filter still applies).
AvailabilityMode = Literal["sleeper", "heuristic", "none"]

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


class Matchup(BaseModel):
    """One entry from ``/v1/league/<id>/matchups/<week>``.

    The matchup archive is the only Sleeper endpoint that records a
    roster *as it stood in that week* — ``players``/``starters`` here
    are historical, unlike ``/rosters`` which always returns current
    state. ``points`` is Sleeper's own total for the week, kept as a
    cross-check; we recompute points from snapshot stats so model and
    actual are measured with the same math.
    """

    model_config = ConfigDict(frozen=True)

    roster_id: int
    matchup_id: int | None = None
    players: tuple[str, ...] = ()
    starters: tuple[str, ...] = ()
    points: float | None = None


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
    # week -> player_id -> stat-level projection, same stat codes as
    # weekly_stats. Sleeper publishes these pre-kickoff, so the leakage
    # contract is: predicting week W may see projections for weeks <= W
    # (stats stay strictly < W). Empty for snapshots without projection
    # files.
    weekly_projections: dict[int, dict[str, dict[str, float]]] = Field(
        default_factory=dict
    )
    prior_season_stats: dict[str, dict[str, float]] = Field(default_factory=dict)
    # week -> team -> opponent, both directions of every game. Built from
    # the optional ``schedule.json`` artifact; empty for snapshots taken
    # before the loader learned to fetch the schedule.
    schedule: dict[int, dict[str, str]] = Field(default_factory=dict)
    # week -> teams playing at home that week. Same source and caveats
    # as ``schedule``; consumed by scoring features (home-field flag).
    home_teams: dict[int, frozenset[str]] = Field(default_factory=dict)
    # Manifest ``snapshot_finished_at``, used as a cache-invalidation
    # token by consumers that memoise work derived from this snapshot
    # (e.g. the scoring-model build cache). None for legacy manifests.
    snapshot_version: str | None = None


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
