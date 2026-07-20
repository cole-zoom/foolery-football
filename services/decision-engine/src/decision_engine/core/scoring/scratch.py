"""Scratch scoring model — a sleeperless blend (milestone 4 PRD).

Display name: "Homegrown Forecast" (built entirely from our own archive,
no Sleeper projection).

Everything is built from our own archive: box scores and the season
schedule. No field of ``weekly_projections`` is read here. The model is
context's skeleton (walk-forward training in the factory, lazy
per-league ridge fits, naive's spread/confidence/risk) with the three
Sleeper ingredients we *can* reconstruct:

- **opponent adjustment** — trailing fantasy points each defense has
  allowed to each position (weeks < W), shrunk toward the league mean;
- **volume forecasting** — recency-weighted opportunity counts
  (targets + carries; attempts for QBs), which are far more stable than
  yardage/TDs;
- **game context** — home/away from the schedule.

Positions QB/RB/WR/TE get the ridge; K and DEF get an
opponent-adjusted recency-weighted mean (their weekly signal is too
thin for a regression to add anything). Rookies with zero history stay
at the zero/low fallback — without projections there is no honest
signal, and inventing one would be the thing this model exists not to
do.

Target week resolution: the pipeline trims the snapshot to weeks < W,
so W = ``max(weeks_included) + 1`` (week 1 when nothing is trimmed in —
prior-season fallback history, neutral opponent ratio).
"""

from __future__ import annotations

import logging
from typing import Final

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
from decision_engine.core.scoring.context import (
    TARGET_CODE,
    TREND_PRIOR,
    TREND_RECENT,
    _PositionFit,
    _predict,
    _ridge_fit,
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

# Positions the ridge covers. K/DEF fall through to the
# opponent-adjusted mean path.
RIDGE_POSITIONS: Final[tuple[str, ...]] = ("QB", "RB", "WR", "TE")
MEAN_POSITIONS: Final[tuple[str, ...]] = ("K", "DEF")
MIN_ROWS_PER_POSITION: Final[int] = 50
# Exponential recency half-life, in weeks, for points and volume.
# Swept on the 2024 backtest: hl in {1, 1.5, 2, 3, 4, 6, inf} — MAE
# improves monotonically toward short half-lives and flattens at 1-2;
# 2.0 keeps the best top-K precision of the flat region.
HALF_LIFE_WEEKS: Final[float] = 2.0
# Shrinkage for the defense-allowed index: games of pseudo-history at
# the league-average rate a defense starts the season with.
DEF_SHRINK_GAMES: Final[float] = 4.0
# Opponent multiplier clamp for the K/DEF mean path — trailing defense
# tables are noisy; never swing a kicker by more than this.
OPP_CLAMP: Final[tuple[float, float]] = (0.75, 1.25)

RUSH_ATT: Final[str] = "rush_att"
PASS_ATT: Final[str] = "pass_att"

# Feature layout (training and prediction share it):
# [recency-weighted mean pts, flat sample mean, weekly stddev,
#  recency-weighted opportunity volume, target-share trend,
#  opponent-allowed ratio, home flag]
# (A recency*(ratio-1) interaction form was swept on 2024 and lost on
# top-K precision with no MAE gain — the raw ratio stays.)
N_FEATURES: Final[int] = 7


def build(snapshot: SnapshotData) -> ScoreFn:
    """Factory entrypoint. Precomputes league-agnostic tables; fits lazily.

    Captures derived tables only (corpus rows, per-(defense, position,
    week) stat sums, schedule maps) — never the ``SnapshotData`` itself.
    """

    season = snapshot.season
    target_week = max(snapshot.weeks_included) + 1 if snapshot.weeks_included else 1
    schedule = snapshot.schedule
    home_teams = snapshot.home_teams

    position_of: dict[str, str] = {}
    team_of: dict[str, str] = {}
    for pid, player in snapshot.players.items():
        pos = player.position or (
            player.fantasy_positions[0] if player.fantasy_positions else None
        )
        if pos in RIDGE_POSITIONS or pos in MEAN_POSITIONS:
            position_of[pid] = pos
            if player.team:
                team_of[pid] = player.team

    # Corpus: pid -> week-sorted [(week, stats)]; ridge positions only.
    corpus: dict[str, list[tuple[int, dict[str, float]]]] = {}
    # Fantasy points are linear in stats, so per-(defense, position,
    # week) *stat sums* are league-agnostic; they get scored under each
    # league's rules at fit time.
    allowed: dict[tuple[str, str], dict[int, dict[str, float]]] = {}
    # team -> week -> team-total targets (target-share denominator).
    team_targets: dict[str, dict[int, float]] = {}

    for week in sorted(snapshot.weekly_stats):
        week_games = schedule.get(week, {})
        for pid, stats in snapshot.weekly_stats[week].items():
            pos = position_of.get(pid)
            if pos is None:
                continue
            team = team_of.get(pid)
            if pos in RIDGE_POSITIONS:
                corpus.setdefault(pid, []).append((week, stats))
                tgt = stats.get(TARGET_CODE, 0.0)
                if team and tgt:
                    by_week = team_targets.setdefault(team, {})
                    by_week[week] = by_week.get(week, 0.0) + tgt
            opponent = week_games.get(team) if team else None
            if opponent:
                agg = allowed.setdefault((opponent, pos), {}).setdefault(week, {})
                for code, val in stats.items():
                    agg[code] = agg.get(code, 0.0) + val

    prior_by_position = bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )

    # league scoring key -> (per-position fits, defense index).
    _LeagueTables = tuple[dict[str, _PositionFit], "_DefenseIndex"]
    tables_by_league: dict[tuple[tuple[str, float], ...], _LeagueTables] = {}

    def tables_for(league_scoring: ScoringSettings) -> _LeagueTables:
        key = tuple(sorted(league_scoring.items()))
        cached = tables_by_league.get(key)
        if cached is not None:
            return cached
        defense = _DefenseIndex(allowed, league_scoring)
        fits = _fit_all_positions(
            corpus,
            position_of,
            team_of,
            team_targets,
            defense,
            schedule,
            home_teams,
            league_scoring,
        )
        tables_by_league[key] = (fits, defense)
        return tables_by_league[key]

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

        naive_mean = sum(sample) / len(sample)
        if len(sample) >= 2:
            spread = sample_stddev(sample, naive_mean)
        else:
            spread = position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
            notes.append("variance from position prior (1 sample)")

        pos = position_of.get(player.player_id) or (
            player.position
            or (player.fantasy_positions[0] if player.fantasy_positions else None)
        )
        fits, defense = tables_for(league_scoring)
        opponent = _opponent_of(player.team, target_week, schedule)
        opp_ratio = (
            defense.ratio(opponent, pos, target_week)
            if opponent and pos
            else 1.0
        )

        mean = naive_mean
        if pos in RIDGE_POSITIONS and this_season_weeks:
            fit = fits.get(pos)
            if fit is not None:
                feats = _features(
                    [(w.week, w.stats) for w in sorted(this_season_weeks, key=lambda x: x.week)],
                    pos,
                    team_targets.get(player.team or "", {}),
                    league_scoring,
                    opp_ratio,
                    _home_flag(player.team, target_week, home_teams),
                )
                mean = _predict(fit, feats)
                notes.append(
                    f"scratch: {pos} regression (n={fit.n_rows}), "
                    f"opp x{opp_ratio:.2f}"
                )
            else:
                notes.append(f"scratch: naive fallback (no {pos} fit)")
        elif pos in MEAN_POSITIONS and this_points:
            recency = _recency_mean(
                [(w.week, weekly_points(w.stats, league_scoring)) for w in this_season_weeks]
            )
            lo, hi = OPP_CLAMP
            clamped = min(hi, max(lo, opp_ratio))
            mean = recency * clamped
            notes.append(f"scratch: {pos} recency mean, opp x{clamped:.2f}")
        else:
            reason = "no current-season data" if pos in RIDGE_POSITIONS else "prior-season mean"
            notes.append(f"scratch: naive fallback ({reason})")

        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=spread,
            risk_adjusted_score=risk_adjust(mean, spread, risk),
            confidence=confidence_for(len(this_season_weeks)),
            notes=tuple(notes),
        )

    return score_player


class _DefenseIndex:
    """Trailing points-allowed-to-position per defense, shrunk + normalised.

    ``ratio(team, pos, week)`` -> how generous this defense has been to
    the position over weeks < ``week``, relative to the league average
    (1.0 = average, above = allows more). Shrunk toward 1.0 by
    ``DEF_SHRINK_GAMES`` so early-season tables don't whipsaw.
    """

    def __init__(
        self,
        allowed: dict[tuple[str, str], dict[int, dict[str, float]]],
        league_scoring: ScoringSettings,
    ) -> None:
        # (team, pos) -> week -> points allowed that week.
        self._points: dict[tuple[str, str], dict[int, float]] = {
            key: {w: weekly_points(agg, league_scoring) for w, agg in by_week.items()}
            for key, by_week in allowed.items()
        }
        self._cache: dict[tuple[str, str, int], float] = {}

    def ratio(self, team: str, pos: str, week: int) -> float:
        key = (team, pos, week)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        league_rates: list[float] = []
        team_pts = 0.0
        team_games = 0
        for (t, p), by_week in self._points.items():
            if p != pos:
                continue
            pts = [v for w, v in by_week.items() if w < week]
            if not pts:
                continue
            league_rates.append(sum(pts) / len(pts))
            if t == team:
                team_pts = sum(pts)
                team_games = len(pts)

        if not league_rates or team_games == 0:
            self._cache[key] = 1.0
            return 1.0
        league_avg = sum(league_rates) / len(league_rates)
        if league_avg <= 0:
            self._cache[key] = 1.0
            return 1.0
        shrunk = (team_pts + DEF_SHRINK_GAMES * league_avg) / (
            team_games + DEF_SHRINK_GAMES
        )
        out = shrunk / league_avg
        self._cache[key] = out
        return out


def _opponent_of(
    team: str | None, week: int, schedule: dict[int, dict[str, str]]
) -> str | None:
    if team is None:
        return None
    return (schedule.get(week) or {}).get(team)


def _home_flag(
    team: str | None, week: int, home_teams: dict[int, frozenset[str]]
) -> float:
    homes = home_teams.get(week)
    if team is None or homes is None:
        return 0.5  # unknown — sit between home and away
    return 1.0 if team in homes else 0.0


def _recency_mean(week_points: list[tuple[int, float]]) -> float:
    """Exponential recency-weighted mean; assumes non-empty."""

    latest = max(w for w, _ in week_points)
    num = den = 0.0
    for w, pts in week_points:
        weight = 0.5 ** ((latest - w) / HALF_LIFE_WEEKS)
        num += weight * pts
        den += weight
    return num / den


def _opportunity(stats: dict[str, float], pos: str) -> float:
    """Volume proxy: touches/targets for skill players, dropbacks for QBs."""

    if pos == "QB":
        return stats.get(PASS_ATT, 0.0) + stats.get(RUSH_ATT, 0.0)
    return stats.get(TARGET_CODE, 0.0) + stats.get(RUSH_ATT, 0.0)


def _features(
    weeks: list[tuple[int, dict[str, float]]],
    pos: str,
    team_week_targets: dict[int, float],
    league_scoring: ScoringSettings,
    opp_ratio: float,
    home: float,
) -> tuple[float, ...]:
    """Feature vector from week-ascending (week, stats) rows. Non-empty."""

    points = [(w, weekly_points(s, league_scoring)) for w, s in weeks]
    flat = sum(p for _, p in points) / len(points)
    recency = _recency_mean(points)
    std = sample_stddev([p for _, p in points], flat) if len(points) >= 2 else 0.0
    volume = _recency_mean([(w, _opportunity(s, pos)) for w, s in weeks])

    shares: list[float] = []
    for w, s in weeks:
        team_total = team_week_targets.get(w, 0.0)
        tgt = s.get(TARGET_CODE, 0.0)
        shares.append(tgt / team_total if team_total > 0 else 0.0)
    recent_shares = shares[-TREND_RECENT:]
    prior_shares = shares[-(TREND_RECENT + TREND_PRIOR) : -TREND_RECENT]
    if prior_shares and pos != "QB":
        trend = sum(recent_shares) / len(recent_shares) - sum(prior_shares) / len(
            prior_shares
        )
    else:
        trend = 0.0

    return (recency, flat, std, volume, trend, opp_ratio, home)


def _fit_all_positions(
    corpus: dict[str, list[tuple[int, dict[str, float]]]],
    position_of: dict[str, str],
    team_of: dict[str, str],
    team_targets: dict[str, dict[int, float]],
    defense: _DefenseIndex,
    schedule: dict[int, dict[str, str]],
    home_teams: dict[int, frozenset[str]],
    league_scoring: ScoringSettings,
) -> dict[str, _PositionFit]:
    """Walk-forward rows, one ridge fit per ridge position.

    Row for player-week W: features from weeks < W (opponent ratio and
    home flag are W's own — both knowable before kickoff), target =
    week-W points. Players absent from week W contribute no row, so the
    model learns points-when-playing, matching the lineup decision.
    """

    rows: dict[str, list[tuple[tuple[float, ...], float]]] = {
        p: [] for p in RIDGE_POSITIONS
    }

    for pid, weeks in corpus.items():
        if len(weeks) < 2:
            continue
        pos = position_of[pid]
        team = team_of.get(pid)
        team_week_targets = team_targets.get(team or "", {})
        for j in range(1, len(weeks)):
            week_j = weeks[j][0]
            opponent = _opponent_of(team, week_j, schedule)
            opp_ratio = defense.ratio(opponent, pos, week_j) if opponent else 1.0
            feats = _features(
                weeks[:j],
                pos,
                team_week_targets,
                league_scoring,
                opp_ratio,
                _home_flag(team, week_j, home_teams),
            )
            rows[pos].append((feats, weekly_points(weeks[j][1], league_scoring)))

    fits: dict[str, _PositionFit] = {}
    for pos, pos_rows in rows.items():
        if len(pos_rows) < MIN_ROWS_PER_POSITION:
            log.info(
                "scratch: %s has %d training rows (<%d); naive fallback",
                pos,
                len(pos_rows),
                MIN_ROWS_PER_POSITION,
            )
            continue
        fits[pos] = _ridge_fit(pos_rows)
    return fits
