"""Blend scoring model — Sleeper's weekly projection, context's spread.

PRD 3.2, resolved by its own acceptance clause. The PRD proposed
``mean = w * history + (1 - w) * projection`` with ``w = n / (n + k)``,
tuned on the 2024 backtest. The tuning verdict was unambiguous: every
step toward the projection improved startable-MAE monotonically
(k=2: 6.40 -> k=8: 6.31 -> k=40: 6.20 -> raw projection: 5.96), and a
per-player residual calibration of the projection was a wash (5.955 vs
5.956). The history backbone adds nothing to the *mean* — so, per the
PRD ("if blend cannot beat the raw projection, ship the simpler thing
and say so"), the mean **is** the week-W projection.

History still does two jobs the projection can't:

- **spread** — Sleeper publishes no uncertainty, so the risk knob runs
  on context's per-player weekly stddev (position prior when the player
  has no sample);
- **fallback** — players without a meaningful week-W projection entry
  score as pure context, and snapshots with no projections at all
  (pre-3.1 archives, plain fixtures) degrade this model to exactly
  context.

Target week resolution: the pipeline's trimmed snapshot carries
projections for weeks <= W and stats for weeks < W, so the target week
is ``max(weekly_projections)``. The projection is stat-level and gets
scored under each league's own rules via ``weekly_points`` — never
Sleeper's precomputed ``pts_*``.
"""

from __future__ import annotations

from typing import Final

from decision_engine.core.scoring import context
from decision_engine.core.scoring.common import (
    bucket_prior_stats_by_position,
    position_prior_stddev,
    risk_adjust,
    weekly_points,
)
from decision_engine.core.scoring.protocol import ScoreFn
from decision_engine.types import (
    Confidence,
    Player,
    PlayerScore,
    ScoringSettings,
    SnapshotData,
    WeeklyStats,
)

# A projection entry without a real games-played signal is ADP noise,
# not a forecast — same threshold as the pipeline availability gate.
MIN_GP: Final[float] = 0.5

_CONFIDENCE_BUMP: Final[dict[Confidence, Confidence]] = {
    "low": "medium",
    "medium": "high",
    "high": "high",
}


def build(snapshot: SnapshotData) -> ScoreFn:
    """Factory entrypoint. Captures the target week's projection table.

    Per the ``ScoreModelFactory`` contract the closure holds derived
    tables only — the week-W projection dict and the prior-season
    position buckets — never the ``SnapshotData`` itself.
    """

    target_week = max(snapshot.weekly_projections) if snapshot.weekly_projections else None
    projection_table = (
        snapshot.weekly_projections[target_week] if target_week is not None else {}
    )
    prior_by_position = bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )
    context_score = context.build(snapshot)

    def score_player(
        player: Player,
        stats_history: list[WeeklyStats],
        league_scoring: ScoringSettings,
        risk: float,
    ) -> PlayerScore:
        base = context_score(player, stats_history, league_scoring, risk)
        if target_week is None:
            return base

        entry = projection_table.get(player.player_id)
        if entry is None or entry.get("gp", 0.0) < MIN_GP:
            return base.model_copy(
                update={"notes": (*base.notes, "no weekly projection")}
            )

        mean = weekly_points(entry, league_scoring)
        if "no historical data" in base.notes:
            # Context's zero/low dead-end (rookies, fresh signings): the
            # position-bucket spread keeps the risk knob meaningful.
            spread = position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
        else:
            spread = base.projected_variance

        # The projection is independent evidence that the player plays
        # and roughly how much — one confidence level over context's.
        confidence = _CONFIDENCE_BUMP[base.confidence]
        notes = (
            *base.notes,
            f"blend: sleeper proj {mean:.1f} (mean); spread from history",
        )
        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=spread,
            risk_adjusted_score=risk_adjust(mean, spread, risk),
            confidence=confidence,
            notes=notes,
        )

    return score_player
