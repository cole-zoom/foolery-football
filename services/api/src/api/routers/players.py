"""GET /players/{player_id}/stats — full weekly breakdown for one player.

Feeds the React detail drawer. Uses the same scoring math the decision
engine uses for the rank, but exposes the per-week points so the user
can see *why* the model is recommending it. League scoring weights have
to be passed in (different leagues = different points).
"""

from __future__ import annotations

import math

from decision_engine.clients.snapshot_reader import SnapshotMissingError
from decision_engine.core.league_fetch import resolve_state
from decision_engine.providers import sleeper
from decision_engine.types import SnapshotData
from ffdm_app.types import LiveState
from fastapi import APIRouter, HTTPException, Query

from api.deps import (
    HttpClientDep,
    PrepareSeasonDep,
    SettingsDep,
    SnapshotReaderDep,
)
from api.hydrate import player_to_wire
from api.schemas import PlayerStatsOut, WeeklyStatLineOut

router = APIRouter(tags=["players"])


@router.get("/players/{player_id}/stats", response_model=PlayerStatsOut)
def get_player_stats(
    player_id: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    league_id: str = Query(
        ..., description="League whose scoring weights to apply when computing points."
    ),
    season: int | None = Query(default=None),
    week: int | None = Query(
        default=None,
        description=(
            "If set, only return weekly lines strictly before this week — "
            "matches the replay semantics the decision engine uses for scoring."
        ),
    ),
) -> PlayerStatsOut:
    state = resolve_state(http, None)
    resolved_season = season if season is not None else state.season
    prepare_season(
        resolved_season,
        LiveState(season=state.season, week=state.week),
    )
    snapshot = snapshot_reader.load(resolved_season)

    player = snapshot.players.get(player_id)
    if player is None:
        raise HTTPException(404, f"player {player_id!r} not in snapshot")

    league_payload = http.get_json(f"/v1/league/{league_id}")
    league = sleeper.validate_league(league_payload)
    scoring = league.scoring_settings

    weeks = _weekly_lines(player_id, snapshot, scoring, through_week=week)
    using_prior_season = False
    prior_season: int | None = None
    # Week 1 (or any case where the current-season trim is empty): show the
    # prior season's per-week breakdown so the drawer isn't blank. Matches
    # the scoring fallback in decision_engine.core.pipeline.
    if not weeks:
        try:
            prior_snapshot = snapshot_reader.load(resolved_season - 1)
        except SnapshotMissingError:
            prior_snapshot = None
        if prior_snapshot is not None:
            weeks = _weekly_lines(player_id, prior_snapshot, scoring, through_week=None)
            if weeks:
                using_prior_season = True
                prior_season = prior_snapshot.season

    points = [w.points for w in weeks]
    season_total = sum(points)
    games = len(points)
    ppg = season_total / games if games else 0.0
    mean = ppg
    stddev = _stddev(points, mean) if games >= 2 else 0.0

    return PlayerStatsOut(
        player=player_to_wire(player, headshot_base=settings.headshot_base_url),
        season=resolved_season,
        weeks=weeks,
        season_total_points=season_total,
        games_played=games,
        points_per_game=ppg,
        mean=mean,
        stddev=stddev,
        using_prior_season=using_prior_season,
        prior_season=prior_season,
    )


def _weekly_lines(
    player_id: str,
    snapshot: SnapshotData,
    scoring: dict[str, float],
    *,
    through_week: int | None,
) -> list[WeeklyStatLineOut]:
    out: list[WeeklyStatLineOut] = []
    for week in sorted(snapshot.weekly_stats.keys()):
        if through_week is not None and week >= through_week:
            continue
        stats = snapshot.weekly_stats[week].get(player_id)
        if not stats:
            continue
        points = sum(weight * stats.get(code, 0.0) for code, weight in scoring.items())
        out.append(WeeklyStatLineOut(week=week, points=points, stats=stats))
    return out


def _stddev(sample: list[float], mean: float) -> float:
    n = len(sample)
    variance = sum((x - mean) ** 2 for x in sample) / (n - 1)
    return math.sqrt(variance)
