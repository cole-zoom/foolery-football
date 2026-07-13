"""Naive baseline scoring model — PRD 2.2.

Deliberately simple. The buddies who actually understand fantasy will
swap it for something better; this exists to be the floor they beat.
It stays registered permanently as the control: the context model is
this model plus extra features, so beating it is a like-for-like test.

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

The shared math lives in ``common.py`` (also consumed by the context
model); this module owns only the orchestration.
"""

from __future__ import annotations

from decision_engine.core.scoring.common import (
    FALLBACK_VARIANCE,
    ZERO_DATA_VARIANCE,
    bucket_prior_stats_by_position,
    confidence_for,
    position_prior_stddev,
    risk_adjust,
    sample_stddev,
    select_sample,
    weekly_points,
)
from decision_engine.core.scoring.protocol import ScoreFn
from decision_engine.types import (
    Player,
    PlayerScore,
    ScoringSettings,
    SnapshotData,
    WeeklyStats,
)

__all__ = ["FALLBACK_VARIANCE", "ZERO_DATA_VARIANCE", "build"]


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
    prior_by_position = bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )
    # Capture only what score_player needs — closing over the whole
    # snapshot would pin it in memory for as long as the build cache
    # holds this ScoreFn (see core/scoring/__init__.py).
    season = snapshot.season

    def score_player(
        player: Player,
        stats_history: list[WeeklyStats],
        league_scoring: ScoringSettings,
        risk: float,
    ) -> PlayerScore:
        notes: list[str] = []

        this_season_weeks = [w for w in stats_history if w.season == season]
        prior_season_weeks = [w for w in stats_history if w.season != season]

        if this_season_weeks or prior_season_weeks:
            this_points = [
                weekly_points(w.stats, league_scoring) for w in this_season_weeks
            ]
            prior_points = [
                weekly_points(w.stats, league_scoring) for w in prior_season_weeks
            ]
            sample = select_sample(this_points, prior_points)
        else:
            sample = []

        if not sample:
            notes.append("no historical data")
            return PlayerScore(
                player_id=player.player_id,
                projected_mean=0.0,
                projected_variance=ZERO_DATA_VARIANCE,
                risk_adjusted_score=risk_adjust(0.0, ZERO_DATA_VARIANCE, risk),
                confidence="low",
                notes=tuple(notes),
            )

        mean = sum(sample) / len(sample)
        if len(sample) >= 2:
            variance = sample_stddev(sample, mean)
        else:
            # 1 sample — fall back to position-bucket prior stddev.
            variance = position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
            notes.append("variance from position prior (1 sample)")

        confidence = confidence_for(len(this_season_weeks))
        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=variance,
            risk_adjusted_score=risk_adjust(mean, variance, risk),
            confidence=confidence,
            notes=tuple(notes),
        )

    return score_player
