"""Context scoring model — naive's skeleton with a learned mean.

Design brief: ``docs/pdfs/Proposed Scoring Model.pdf``. The naive
model's flat rolling mean is replaced by a per-position ridge
regression whose inputs are naive's own mean/variance *plus* target
volume and target-share trend (the "build this first" tier-1 feature).
Everything else — sample window, sigma estimate, confidence tiers, risk
formula, output shape — is naive's, imported from ``common.py``, so
naive remains the degenerate case of this model with one feature.

Training happens inside the factory, walk-forward over the snapshot
itself: for every completed week W the features come from weeks
strictly before W and the target is week W's fantasy points. That
mirrors the ``_snapshot_as_of`` replay contract, so the model is
leakage-safe by construction and "retraining" is simply loading a
fresher snapshot.

One wrinkle: the regression target is fantasy points under *the
league's* scoring settings, which only arrive per-call. The fit is
therefore lazy — first call for a given scoring dict fits all
positions (a few ms of pure-python normal equations) and caches the
coefficients. Concurrent first calls may fit twice; the results are
identical and the race is benign.
"""

from __future__ import annotations

import logging
from typing import Final, NamedTuple

from decision_engine.core.scoring.common import (
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

log = logging.getLogger(__name__)

# Positions the regression covers: the ones the tier-1 target features
# actually describe. QB/K/DEF gain nothing from target volume/share (a
# fit there is naive-plus-noise, confirmed by backtest) — they fall
# back to the naive estimate until their own features land (QB form is
# build-order step 3 in the design brief).
POSITIONS: Final[tuple[str, ...]] = ("RB", "WR", "TE")
TARGET_CODE: Final[str] = "rec_tgt"
# Fewer training rows than this for a position -> naive fallback. Keeps
# early-season fits (weeks 1-2) from hallucinating on tiny samples.
MIN_ROWS_PER_POSITION: Final[int] = 50
# Chosen by a small sweep on the 2025 replay (see PR / backtest script):
# lambda in [0.1, 100] barely moves startable-MAE; 5.0 gave the best
# top-K precision at TE/WR without hurting RB.
RIDGE_LAMBDA: Final[float] = 5.0
# Rolling target volume window, and the trend split: mean share of the
# last TREND_RECENT weeks minus mean share of the TREND_PRIOR weeks
# before them (the brief's "last-2-week share minus prior-3-week share").
VOLUME_WINDOW: Final[int] = 3
TREND_RECENT: Final[int] = 2
TREND_PRIOR: Final[int] = 3

# Feature layout (order matters — training and prediction share it):
# [naive mean, naive stddev, target volume, target-share trend].
N_FEATURES: Final[int] = 4


class _WeekObs(NamedTuple):
    """One completed week of one player, in feature-input form."""

    week: int
    points: float
    targets: float
    target_share: float


class _PositionFit(NamedTuple):
    """Standardised ridge coefficients for one position."""

    feature_means: tuple[float, ...]
    feature_stds: tuple[float, ...]
    # beta[0] is the intercept; beta[1:] pair with the standardised features.
    beta: tuple[float, ...]
    n_rows: int


def build(snapshot: SnapshotData) -> ScoreFn:
    """Factory entrypoint. Precomputes feature tables; fits lazily per league.

    Captures compact derived tables only — never the snapshot itself —
    per the ``ScoreModelFactory`` contract. The training corpus keeps
    references to the *offensive players'* per-week stat dicts (needed
    to compute league-specific points at fit time); IDP and inactive
    rows are dropped.
    """

    season = snapshot.season

    position_of: dict[str, str] = {}
    team_of: dict[str, str] = {}
    for pid, player in snapshot.players.items():
        pos = player.position or (
            player.fantasy_positions[0] if player.fantasy_positions else None
        )
        if pos in POSITIONS:
            position_of[pid] = pos
            if player.team:
                team_of[pid] = player.team

    # Corpus: pid -> week-sorted [(week, stats)] for covered positions.
    corpus: dict[str, list[tuple[int, dict[str, float]]]] = {}
    # team -> week -> total targets thrown to that team's players. The
    # denominator of target share, both at fit and at predict time.
    team_targets: dict[str, dict[int, float]] = {}
    for week in sorted(snapshot.weekly_stats):
        for pid, stats in snapshot.weekly_stats[week].items():
            if pid not in position_of:
                continue
            corpus.setdefault(pid, []).append((week, stats))
            tgt = stats.get(TARGET_CODE, 0.0)
            team = team_of.get(pid)
            if team and tgt:
                by_week = team_targets.setdefault(team, {})
                by_week[week] = by_week.get(week, 0.0) + tgt

    prior_by_position = bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )

    # league scoring key -> per-position fit (None = not enough rows).
    fits_by_league: dict[tuple[tuple[str, float], ...], dict[str, _PositionFit]] = {}

    def fit_for(league_scoring: ScoringSettings) -> dict[str, _PositionFit]:
        key = tuple(sorted(league_scoring.items()))
        cached = fits_by_league.get(key)
        if cached is not None:
            return cached
        fits = _fit_all_positions(
            corpus, position_of, team_of, team_targets, league_scoring
        )
        fits_by_league[key] = fits
        return fits

    def score_player(
        player: Player,
        stats_history: list[WeeklyStats],
        league_scoring: ScoringSettings,
        risk: float,
    ) -> PlayerScore:
        notes: list[str] = []

        this_season_weeks = [w for w in stats_history if w.season == season]
        prior_season_weeks = [w for w in stats_history if w.season != season]

        this_points = [weekly_points(w.stats, league_scoring) for w in this_season_weeks]
        prior_points = [weekly_points(w.stats, league_scoring) for w in prior_season_weeks]
        sample = select_sample(this_points, prior_points)

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

        # sigma is naive's, verbatim: the regression predicts the mean only.
        naive_mean = sum(sample) / len(sample)
        if len(sample) >= 2:
            variance = sample_stddev(sample, naive_mean)
        else:
            variance = position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
            notes.append("variance from position prior (1 sample)")

        pos = player.position or (
            player.fantasy_positions[0] if player.fantasy_positions else None
        )
        fit = fit_for(league_scoring).get(pos) if pos is not None else None

        if fit is not None and this_season_weeks:
            obs = _observations(
                this_season_weeks, team_targets.get(player.team or "", {}), league_scoring
            )
            mean = _predict(fit, _features(obs))
            notes.append(f"context: {pos} regression (n={fit.n_rows})")
        else:
            mean = naive_mean
            reason = (
                "no current-season data"
                if fit is not None
                else f"no fit for position {pos or '?'}"
            )
            notes.append(f"context: naive fallback ({reason})")

        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=variance,
            risk_adjusted_score=risk_adjust(mean, variance, risk),
            confidence=confidence_for(len(this_season_weeks)),
            notes=tuple(notes),
        )

    return score_player


def _observations(
    weeks: list[WeeklyStats],
    team_week_targets: dict[int, float],
    league_scoring: ScoringSettings,
) -> list[_WeekObs]:
    """Current-season history -> feature-input rows, week-ascending."""

    out: list[_WeekObs] = []
    for w in sorted(weeks, key=lambda x: x.week):
        tgt = w.stats.get(TARGET_CODE, 0.0)
        team_total = team_week_targets.get(w.week, 0.0)
        share = tgt / team_total if team_total > 0 else 0.0
        out.append(
            _WeekObs(
                week=w.week,
                points=weekly_points(w.stats, league_scoring),
                targets=tgt,
                target_share=share,
            )
        )
    return out


def _features(obs: list[_WeekObs]) -> tuple[float, ...]:
    """Feature vector from week-ascending observations. Assumes obs non-empty."""

    points = [o.points for o in obs]
    mean = sum(points) / len(points)
    std = sample_stddev(points, mean) if len(points) >= 2 else 0.0

    recent = obs[-VOLUME_WINDOW:]
    volume = sum(o.targets for o in recent) / len(recent)

    recent_shares = [o.target_share for o in obs[-TREND_RECENT:]]
    prior_shares = [
        o.target_share for o in obs[-(TREND_RECENT + TREND_PRIOR) : -TREND_RECENT]
    ]
    if prior_shares:
        trend = sum(recent_shares) / len(recent_shares) - sum(prior_shares) / len(
            prior_shares
        )
    else:
        trend = 0.0

    return (mean, std, volume, trend)


def _fit_all_positions(
    corpus: dict[str, list[tuple[int, dict[str, float]]]],
    position_of: dict[str, str],
    team_of: dict[str, str],
    team_targets: dict[str, dict[int, float]],
    league_scoring: ScoringSettings,
) -> dict[str, _PositionFit]:
    """Walk-forward training rows, then one ridge fit per position.

    For each player-week W with at least one earlier week of data:
    features from weeks < W, target = points scored in week W. Players
    absent from week W (bye, injury) contribute no row for W — the
    model learns "points when playing", matching what the lineup
    decision needs.
    """

    rows: dict[str, list[tuple[tuple[float, ...], float]]] = {p: [] for p in POSITIONS}

    for pid, weeks in corpus.items():
        if len(weeks) < 2:
            continue
        pos = position_of[pid]
        team_week_targets = team_targets.get(team_of.get(pid, ""), {})
        obs = [
            _WeekObs(
                week=week,
                points=weekly_points(stats, league_scoring),
                targets=stats.get(TARGET_CODE, 0.0),
                target_share=(
                    stats.get(TARGET_CODE, 0.0) / team_week_targets[week]
                    if team_week_targets.get(week, 0.0) > 0
                    else 0.0
                ),
            )
            for week, stats in weeks
        ]
        for j in range(1, len(obs)):
            rows[pos].append((_features(obs[:j]), obs[j].points))

    fits: dict[str, _PositionFit] = {}
    for pos, pos_rows in rows.items():
        if len(pos_rows) < MIN_ROWS_PER_POSITION:
            log.info(
                "context: %s has %d training rows (<%d); naive fallback",
                pos,
                len(pos_rows),
                MIN_ROWS_PER_POSITION,
            )
            continue
        fits[pos] = _ridge_fit(pos_rows)
    return fits


def _ridge_fit(
    rows: list[tuple[tuple[float, ...], float]],
    *,
    ridge_lambda: float = RIDGE_LAMBDA,
) -> _PositionFit:
    """Closed-form ridge on standardised features, unpenalised intercept.

    Small enough (a handful of normal equations) that pure python beats
    pulling numpy into the image. Feature count is inferred from the
    rows so wider models (scratch) can reuse this verbatim.
    """

    n = len(rows)
    n_features = len(rows[0][0])
    means = [0.0] * n_features
    for feats, _y in rows:
        for i, f in enumerate(feats):
            means[i] += f / n
    stds = [0.0] * n_features
    for feats, _y in rows:
        for i, f in enumerate(feats):
            stds[i] += (f - means[i]) ** 2 / n
    # Constant columns standardise to all-zeros (std pinned to 1) so the
    # ridge penalty zeroes their coefficient instead of dividing by 0.
    stds = [s**0.5 if s > 1e-12 else 1.0 for s in stds]

    dim = n_features + 1  # intercept + standardised features
    xtx = [[0.0] * dim for _ in range(dim)]
    xty = [0.0] * dim
    for feats, y in rows:
        z = [1.0] + [(f - means[i]) / stds[i] for i, f in enumerate(feats)]
        for a in range(dim):
            xty[a] += z[a] * y
            for b in range(dim):
                xtx[a][b] += z[a] * z[b]
    for a in range(1, dim):  # leave the intercept unpenalised
        xtx[a][a] += ridge_lambda

    beta = _solve(xtx, xty)
    return _PositionFit(
        feature_means=tuple(means),
        feature_stds=tuple(stds),
        beta=tuple(beta),
        n_rows=n,
    )


def _predict(fit: _PositionFit, feats: tuple[float, ...]) -> float:
    out = fit.beta[0]
    for i, f in enumerate(feats):
        out += fit.beta[i + 1] * (f - fit.feature_means[i]) / fit.feature_stds[i]
    return out


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. Mutates copies of inputs.

    The ridge-regularised normal matrix is symmetric positive definite,
    so this cannot hit a zero pivot in practice; the guard is belt and
    braces against degenerate float input.
    """

    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = col
        for r in range(col + 1, n):
            if abs(m[r][col]) > abs(m[pivot][col]):
                pivot = r
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular normal matrix in ridge fit")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x
