"""Shared scoring math used by every model.

Extracted verbatim from ``naive.py`` so the context model can reuse the
exact sample-window, variance-fallback, and risk logic — the design
brief's "naive is the degenerate case" claim only holds if both models
literally share these functions.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

from decision_engine.types import Confidence, Player, ScoringSettings

# Placeholder used when we have literally zero data and no prior. The
# scoring math still wants a non-zero spread so a maximally-risky user
# doesn't get a flat 0.0 across rookies.
ZERO_DATA_VARIANCE: Final[float] = 5.0
# Backup positional stddev if the prior season is missing entirely. One
# stddev that's roughly the spread of a mid-tier flex player.
FALLBACK_VARIANCE: Final[float] = 4.0
HIGH_CONFIDENCE_THRESHOLD: Final[int] = 4
SAMPLE_PAD_TARGET: Final[int] = 4


def weekly_points(stats: dict[str, float], league_scoring: ScoringSettings) -> float:
    """Stat counts -> points via the league's scoring weights.

    Codes missing from ``league_scoring`` contribute zero, per PRD.
    """

    return sum(weight * stats.get(code, 0.0) for code, weight in league_scoring.items())


def select_sample(this_points: list[float], prior_points: list[float]) -> list[float]:
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


def sample_stddev(sample: list[float], mean: float) -> float:
    """Sample stddev (Bessel-corrected). Assumes ``len(sample) >= 2``."""

    n = len(sample)
    variance = sum((x - mean) ** 2 for x in sample) / (n - 1)
    return math.sqrt(variance)


def risk_adjust(mean: float, variance: float, risk: float) -> float:
    """``mean + (risk - 0.5) * 2 * variance`` — see PRD 2.2 §6."""

    return mean + (risk - 0.5) * 2.0 * variance


def confidence_for(this_season_count: int) -> Confidence:
    if this_season_count >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if this_season_count >= 1:
        return "medium"
    return "low"


def bucket_prior_stats_by_position(
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


def position_prior_stddev(
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
            season_points = weekly_points(stats, league_scoring)
            points_per_game.append(season_points / gp)

    if len(points_per_game) < 2:
        return FALLBACK_VARIANCE

    mean = sum(points_per_game) / len(points_per_game)
    return sample_stddev(points_per_game, mean)
