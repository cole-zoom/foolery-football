"""Naive baseline scoring model — PRD 2.2.

Deliberately simple. The buddies who actually understand fantasy will
swap it for something better; this exists to be the floor they beat.

Algorithm (per PRD 2.2):

1. Convert each historical week's stats into points using
   ``league_scoring`` weights.
2. Choose the sample window:
   - If ≥3 weeks of the current season: use this-season only.
   - Else: pad with prior-season per-week points until we have ≥4 or
     run out.
   - If zero data: mean=0, variance=5.0, confidence=low.
3. Mean = arithmetic mean of those weekly points.
4. Variance = sample stddev. With 1 sample, fall back to the
   position-bucket stddev computed once from the prior season.
5. Confidence: >=4 this-season weeks = high, 1-3 = medium, 0 = low.
6. ``score = mean + (risk - 0.5) * 2 * variance``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

from decision_engine.core.scoring.protocol import ScoreFn
from decision_engine.types import (
    Confidence,
    Player,
    PlayerScore,
    ScoringSettings,
    SnapshotData,
    WeeklyStats,
)

# Placeholder used when we have literally zero data and no prior. The
# scoring math still wants a non-zero spread so a maximally-risky user
# doesn't get a flat 0.0 across rookies.
ZERO_DATA_VARIANCE: Final[float] = 5.0
# Backup positional stddev if the prior season is missing entirely. One
# stddev that's roughly the spread of a mid-tier flex player.
FALLBACK_VARIANCE: Final[float] = 4.0
HIGH_CONFIDENCE_THRESHOLD: Final[int] = 4
SAMPLE_PAD_TARGET: Final[int] = 4


def build(snapshot: SnapshotData) -> ScoreFn:
    """Factory entrypoint. Precomputes the position-bucket stddev.

    The position-bucket prior is computed once over the prior season's
    per-game points across all players at each position. See PRD 2.2 §4.
    """

    # We need league scoring to convert prior-season totals to points,
    # but the factory runs before we have league_scoring. So we cache
    # the *raw* prior season stats per position, and recompute the
    # priors per call lazily — cheap, and avoids carrying league
    # scoring through global state.
    prior_by_position = _bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )

    def score_player(
        player: Player,
        stats_history: list[WeeklyStats],
        league_scoring: ScoringSettings,
        risk: float,
    ) -> PlayerScore:
        notes: list[str] = []

        this_season_weeks = [w for w in stats_history if w.season == snapshot.season]
        prior_season_weeks = [w for w in stats_history if w.season != snapshot.season]

        if this_season_weeks or prior_season_weeks:
            this_points = [
                _weekly_points(w.stats, league_scoring) for w in this_season_weeks
            ]
            prior_points = [
                _weekly_points(w.stats, league_scoring) for w in prior_season_weeks
            ]
            sample = _select_sample(this_points, prior_points)
        else:
            sample = []

        if not sample:
            notes.append("no historical data")
            return PlayerScore(
                player_id=player.player_id,
                projected_mean=0.0,
                projected_variance=ZERO_DATA_VARIANCE,
                risk_adjusted_score=_risk_adjust(0.0, ZERO_DATA_VARIANCE, risk),
                confidence="low",
                notes=tuple(notes),
            )

        mean = sum(sample) / len(sample)
        if len(sample) >= 2:
            variance = _sample_stddev(sample, mean)
        else:
            # 1 sample — fall back to position-bucket prior stddev.
            variance = _position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
            notes.append("variance from position prior (1 sample)")

        confidence = _confidence_for(len(this_season_weeks))
        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=variance,
            risk_adjusted_score=_risk_adjust(mean, variance, risk),
            confidence=confidence,
            notes=tuple(notes),
        )

    return score_player


def _weekly_points(stats: dict[str, float], league_scoring: ScoringSettings) -> float:
    """Stat counts -> points via the league's scoring weights.

    Codes missing from ``league_scoring`` contribute zero, per PRD.
    """

    return sum(weight * stats.get(code, 0.0) for code, weight in league_scoring.items())


def _select_sample(this_points: list[float], prior_points: list[float]) -> list[float]:
    """Pick the sample window per PRD step 2.

    No current-season data → use the full prior season (week-1 replay).
    1-2 current weeks → pad with prior up to ``SAMPLE_PAD_TARGET``.
    3+ current weeks → ignore prior.
    """

    if len(this_points) >= 3:
        return list(this_points)
    if not this_points:
        return list(prior_points)
    sample = list(this_points)
    need = max(0, SAMPLE_PAD_TARGET - len(sample))
    if need and prior_points:
        sample.extend(prior_points[:need])
    return sample


def _sample_stddev(sample: list[float], mean: float) -> float:
    """Sample stddev (Bessel-corrected). Assumes ``len(sample) >= 2``."""

    n = len(sample)
    variance = sum((x - mean) ** 2 for x in sample) / (n - 1)
    return math.sqrt(variance)


def _risk_adjust(mean: float, variance: float, risk: float) -> float:
    """``mean + (risk - 0.5) * 2 * variance`` — see PRD 2.2 §6."""

    return mean + (risk - 0.5) * 2.0 * variance


def _confidence_for(this_season_count: int) -> Confidence:
    if this_season_count >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if this_season_count >= 1:
        return "medium"
    return "low"


def _bucket_prior_stats_by_position(
    players: dict[str, Player],
    prior_season_stats: dict[str, dict[str, float]],
) -> dict[str, list[dict[str, float]]]:
    """Group raw prior-season stat lines by player position.

    Returns ``{position: [stat_dict, ...]}``. The stat_dicts are the raw
    Sleeper season-total stat blobs; we'll project them to points
    per-call once we know ``league_scoring``.
    """

    by_pos: dict[str, list[dict[str, float]]] = {}
    for pid, stats in prior_season_stats.items():
        player = players.get(pid)
        if player is None or not player.fantasy_positions:
            continue
        for pos in player.fantasy_positions:
            by_pos.setdefault(pos, []).append(stats)
    return by_pos


def _position_prior_stddev(
    fantasy_positions: Iterable[str],
    prior_by_position: dict[str, list[dict[str, float]]],
    league_scoring: ScoringSettings,
) -> float:
    """Stddev of prior-season per-game points across players in this position.

    Uses Sleeper's ``gp`` (games played) to convert season totals to
    per-game. Players without ``gp`` get skipped (quarantine over drop).
    Falls back to ``FALLBACK_VARIANCE`` if no usable samples.
    """

    points_per_game: list[float] = []
    seen: set[int] = set()
    for pos in fantasy_positions:
        bucket = prior_by_position.get(pos)
        if not bucket:
            continue
        for stats in bucket:
            ident = id(stats)
            if ident in seen:
                continue
            seen.add(ident)
            gp = stats.get("gp", 0.0)
            if gp <= 0:
                continue
            season_points = _weekly_points(stats, league_scoring)
            points_per_game.append(season_points / gp)

    if len(points_per_game) < 2:
        return FALLBACK_VARIANCE

    mean = sum(points_per_game) / len(points_per_game)
    return _sample_stddev(points_per_game, mean)
