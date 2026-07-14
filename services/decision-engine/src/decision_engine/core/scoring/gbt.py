"""GBT scoring model — the full regression-spec feature set on boosted trees.

Implements the "Sleeper Fantasy Football Regression Model Specification":
one gradient-boosted tree regressor per position (QB/RB/WR/TE), trained
walk-forward over the snapshot with every rolling feature shifted by one
game so the predicted week is never seen. Where the spec fixes three
scoring formats (STD/HALF/PPR), this implementation generalises the same
way ``context`` does: the target and all point-based features are
computed under *the league's* scoring dict, fitted lazily per unique
scoring config — the spec's three formats are just three instances.

Feature blocks (18 per position):

- shared: fp_ewma_5, fp_std_5, fp_trend_2v3, games_in_sample,
  prior_season_fp_pg, home_flag, snap_share_3, opp_pos_fpa_index
- QB: pass volume/accuracy/efficiency, rushing usage, team pass rate,
  opponent sack rate
- RB: opportunity volume/share/trend, efficiency, goal-line share,
  opponent RB rush-efficiency index
- WR/TE: target volume/share/trend, air yards, red-zone share, team
  pass volume, team-QB yards per attempt

Deliberately omitted from the spec: ``oline_health_index`` and every
Section-6 injury feature. Both need data the snapshots don't carry
(depth charts; an archived history of ``injury_status`` /
``practice_participation``). They can land once the loader starts
snapshotting those fields.

The regressor is a small pure-python histogram GBT (quantile-binned
features, depth-3 trees, squared loss) — same "no numpy in the image"
constraint the ridge model honours. Missing feature values get a
dedicated bin that always routes left at a split, which is how the
trees absorb thin early-season windows instead of needing imputation.

Sigma, confidence tiers, the risk formula, and every fallback path are
naive's, imported from ``common.py`` — the trees replace the *mean*
only, so a backtest difference against ``context``/``naive`` isolates
the predictor.
"""

from __future__ import annotations

import logging
import math
from bisect import bisect_left, bisect_right
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

POSITIONS: Final[tuple[str, ...]] = ("QB", "RB", "WR", "TE")
# Fewer walk-forward rows than this for a position -> naive fallback.
# Trees overfit small samples harder than ridge, so the bar sits above
# context's 50.
MIN_ROWS_PER_POSITION: Final[int] = 100

# --- GBT hyperparameters. Sized for a few-thousand-row corpus: small
# enough to fit in well under a second per position in pure python,
# regularised enough (depth 3, min-leaf 20) not to memorise it.
N_TREES: Final[int] = 60
LEARNING_RATE: Final[float] = 0.1
MAX_DEPTH: Final[int] = 3
MIN_LEAF: Final[int] = 20
N_BINS: Final[int] = 16
LEAF_LAMBDA: Final[float] = 1.0

# --- Feature windows, straight from the spec.
EWMA_WINDOW: Final[int] = 5
EWMA_MIN_GAMES: Final[int] = 2
STD_MIN_GAMES: Final[int] = 3
TREND_RECENT: Final[int] = 2
TREND_PRIOR: Final[int] = 3
# Shrinkage pseudo-games for the defense-vs-position index (spec: 4).
FPA_PRIOR_GAMES: Final[float] = 4.0
# Pseudo-dropbacks pulling a defense's observed sack rate toward league
# average — roughly four games' worth of dropbacks.
SACK_PRIOR_DROPBACKS: Final[float] = 150.0

NAN: Final[float] = float("nan")

N_FEATURES: Final[int] = 18


class _Game(NamedTuple):
    """One prior active game of one player, points precomputed."""

    week: int
    points: float
    stats: dict[str, float]


class _TeamWeek(NamedTuple):
    """One team's aggregate offensive line for one week."""

    pass_att: float
    rush_att: float
    targets: float
    air_yd: float
    rz_targets: float
    rb_opps: float  # RB carries + RB targets
    rb_rz_att: float
    qb_pass_yd: float
    qb_pass_att: float


class _DefenseTables:
    """Season-to-date opponent strength accumulators (walk-forward safe:
    the fit only reads them for week W after folding in weeks < W)."""

    def __init__(self) -> None:
        # (defense, position) -> [observed points, expected points, games]
        self.fpa: dict[tuple[str, str], list[float]] = {}
        # defense -> [RB rush yards, RB carries, games]
        self.rb_rush: dict[str, list[float]] = {}
        self.league_rb_yd = 0.0
        self.league_rb_att = 0.0
        # defense -> [sacks, dropbacks]
        self.sacks: dict[str, list[float]] = {}
        self.league_sacks = 0.0
        self.league_dropbacks = 0.0

    def fpa_index(self, defense: str | None, position: str) -> float:
        """Spec's shrunk defense-vs-position index; 100 = neutral."""

        if defense is None:
            return NAN
        entry = self.fpa.get((defense, position))
        if entry is None or entry[1] <= 0:
            return 100.0
        raw = entry[0] / entry[1]
        games = entry[2]
        return 100.0 * (games * raw + FPA_PRIOR_GAMES) / (games + FPA_PRIOR_GAMES)

    def rb_eff_index(self, defense: str | None) -> float:
        if defense is None:
            return NAN
        if self.league_rb_att <= 0:
            return 1.0
        league_ypc = self.league_rb_yd / self.league_rb_att
        entry = self.rb_rush.get(defense)
        if entry is None or entry[1] <= 0 or league_ypc <= 0:
            return 1.0
        raw = (entry[0] / entry[1]) / league_ypc
        games = entry[2]
        return (games * raw + FPA_PRIOR_GAMES) / (games + FPA_PRIOR_GAMES)

    def sack_rate(self, defense: str | None) -> float:
        if defense is None:
            return NAN
        if self.league_dropbacks <= 0:
            return 0.0
        league_rate = self.league_sacks / self.league_dropbacks
        entry = self.sacks.get(defense)
        if entry is None:
            return league_rate
        return (entry[0] + SACK_PRIOR_DROPBACKS * league_rate) / (entry[1] + SACK_PRIOR_DROPBACKS)


class _TreeNode(NamedTuple):
    """One node of a fitted tree. ``feature < 0`` marks a leaf."""

    feature: int
    split_bin: int  # binned value <= split_bin goes left (missing bin 0 always left)
    left: int
    right: int
    value: float  # leaf output, learning rate folded in


class _PositionFit(NamedTuple):
    bin_edges: tuple[tuple[float, ...], ...]  # per feature, ascending
    base: float
    trees: tuple[tuple[_TreeNode, ...], ...]
    n_rows: int


class _ScoringFit(NamedTuple):
    """Everything predict-time needs for one league scoring config."""

    fits: dict[str, _PositionFit]
    defense: _DefenseTables
    prior_fp_pg: dict[str, float]


def build(snapshot: SnapshotData) -> ScoreFn:
    """Factory entrypoint. Precomputes team/schedule tables; fits lazily
    per league scoring config, same pattern as ``context.build``."""

    season = snapshot.season

    position_of: dict[str, str] = {}
    team_of: dict[str, str] = {}
    for pid, player in snapshot.players.items():
        pos = player.position or (player.fantasy_positions[0] if player.fantasy_positions else None)
        if pos in POSITIONS:
            position_of[pid] = pos
            if player.team:
                team_of[pid] = player.team

    # week -> [(pid, stats)] for covered positions, weeks ascending.
    by_week: dict[int, list[tuple[str, dict[str, float]]]] = {}
    for week in sorted(snapshot.weekly_stats):
        rows = [
            (pid, stats) for pid, stats in snapshot.weekly_stats[week].items() if pid in position_of
        ]
        if rows:
            by_week[week] = rows

    team_weeks, team_week_order = _team_tables(by_week, position_of, team_of)

    schedule = snapshot.schedule
    home_teams = snapshot.home_teams
    prior_season_stats = snapshot.prior_season_stats
    prior_by_position = bucket_prior_stats_by_position(
        snapshot.players, snapshot.prior_season_stats
    )

    completed = sorted(by_week)
    prediction_week = completed[-1] + 1 if completed else None

    fits_by_league: dict[tuple[tuple[str, float], ...], _ScoringFit] = {}

    def fit_for(league_scoring: ScoringSettings) -> _ScoringFit:
        key = tuple(sorted(league_scoring.items()))
        cached = fits_by_league.get(key)
        if cached is not None:
            return cached
        fit = _fit_all_positions(
            by_week,
            position_of,
            team_of,
            team_weeks,
            team_week_order,
            schedule,
            home_teams,
            prior_season_stats,
            league_scoring,
        )
        fits_by_league[key] = fit
        return fit

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

        # sigma is naive's, verbatim: the trees predict the mean only.
        naive_mean = sum(sample) / len(sample)
        if len(sample) >= 2:
            variance = sample_stddev(sample, naive_mean)
        else:
            variance = position_prior_stddev(
                player.fantasy_positions, prior_by_position, league_scoring
            )
            notes.append("variance from position prior (1 sample)")

        pos = player.position or (player.fantasy_positions[0] if player.fantasy_positions else None)
        scoring_fit = fit_for(league_scoring) if pos in POSITIONS else None
        fit = scoring_fit.fits.get(pos) if scoring_fit is not None and pos else None

        if fit is not None and scoring_fit is not None and pos and this_season_weeks:
            games = [
                _Game(w.week, weekly_points(w.stats, league_scoring), w.stats)
                for w in sorted(this_season_weeks, key=lambda x: x.week)
            ]
            team = player.team
            week = prediction_week if prediction_week is not None else games[-1].week + 1
            opponent = schedule.get(week, {}).get(team) if team else None
            feats = _feature_vector(
                pos,
                games,
                team=team,
                week=week,
                opponent=opponent,
                team_weeks=team_weeks,
                team_week_order=team_week_order,
                home_teams=home_teams,
                defense=scoring_fit.defense,
                prior_fp_pg=scoring_fit.prior_fp_pg.get(player.player_id, NAN),
            )
            mean = _predict(fit, feats)
            notes.append(f"gbt: {pos} boosted trees (n={fit.n_rows})")
        else:
            mean = naive_mean
            if pos not in POSITIONS:
                reason = f"position {pos or '?'} not covered"
            elif fit is None:
                reason = f"no fit for position {pos}"
            else:
                reason = "no current-season data"
            notes.append(f"gbt: naive fallback ({reason})")

        return PlayerScore(
            player_id=player.player_id,
            projected_mean=mean,
            projected_variance=variance,
            risk_adjusted_score=risk_adjust(mean, variance, risk),
            confidence=confidence_for(len(this_season_weeks)),
            notes=tuple(notes),
        )

    return score_player


# --------------------------------------------------------------------------
# Team-week aggregates (scoring-independent, built once per snapshot).


def _team_tables(
    by_week: dict[int, list[tuple[str, dict[str, float]]]],
    position_of: dict[str, str],
    team_of: dict[str, str],
) -> tuple[dict[tuple[str, int], _TeamWeek], dict[str, list[int]]]:
    """-> ((team, week) -> aggregates, team -> ascending game weeks)."""

    acc: dict[tuple[str, int], dict[str, float]] = {}
    for week, rows in by_week.items():
        for pid, stats in rows:
            team = team_of.get(pid)
            if team is None:
                continue
            a = acc.setdefault(
                (team, week),
                {
                    "pass_att": 0.0,
                    "rush_att": 0.0,
                    "targets": 0.0,
                    "air_yd": 0.0,
                    "rz_targets": 0.0,
                    "rb_opps": 0.0,
                    "rb_rz_att": 0.0,
                    "qb_pass_yd": 0.0,
                    "qb_pass_att": 0.0,
                },
            )
            a["pass_att"] += stats.get("pass_att", 0.0)
            a["rush_att"] += stats.get("rush_att", 0.0)
            a["targets"] += stats.get("rec_tgt", 0.0)
            a["air_yd"] += stats.get("rec_air_yd", 0.0)
            a["rz_targets"] += stats.get("rec_rz_tgt", 0.0)
            pos = position_of[pid]
            if pos == "RB":
                a["rb_opps"] += stats.get("rush_att", 0.0) + stats.get("rec_tgt", 0.0)
                a["rb_rz_att"] += stats.get("rush_rz_att", 0.0)
            elif pos == "QB":
                a["qb_pass_yd"] += stats.get("pass_yd", 0.0)
                a["qb_pass_att"] += stats.get("pass_att", 0.0)

    team_weeks = {
        key: _TeamWeek(
            pass_att=vals["pass_att"],
            rush_att=vals["rush_att"],
            targets=vals["targets"],
            air_yd=vals["air_yd"],
            rz_targets=vals["rz_targets"],
            rb_opps=vals["rb_opps"],
            rb_rz_att=vals["rb_rz_att"],
            qb_pass_yd=vals["qb_pass_yd"],
            qb_pass_att=vals["qb_pass_att"],
        )
        for key, vals in acc.items()
    }
    order: dict[str, list[int]] = {}
    for team, week in team_weeks:
        order.setdefault(team, []).append(week)
    for weeks in order.values():
        weeks.sort()
    return team_weeks, order


# --------------------------------------------------------------------------
# Feature extraction. Every helper returns NaN when its window/denominator
# is empty — the trees route NaN through the dedicated missing bin.


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else NAN


def _sum_stat(games: list[_Game], code: str) -> float:
    return sum(g.stats.get(code, 0.0) for g in games)


def _per_game(games: list[_Game], code: str, window: int) -> float:
    recent = games[-window:]
    return _sum_stat(recent, code) / len(recent) if recent else NAN


def _stat_ratio(games: list[_Game], num: str, den: str, window: int) -> float:
    recent = games[-window:]
    d = _sum_stat(recent, den)
    return _sum_stat(recent, num) / d if d > 0 else NAN


def _trend_2v3(values: list[float]) -> float:
    if len(values) < TREND_RECENT + TREND_PRIOR:
        return NAN
    window = values[-(TREND_RECENT + TREND_PRIOR) :]
    return _mean(window[-TREND_RECENT:]) - _mean(window[:TREND_PRIOR])


def _fp_ewma(games: list[_Game]) -> float:
    recent = games[-EWMA_WINDOW:]
    if len(recent) < EWMA_MIN_GAMES:
        return NAN
    weights = range(1, len(recent) + 1)  # oldest -> newest: 1, 2, ...
    total = sum(w * g.points for w, g in zip(weights, recent, strict=True))
    return total / sum(weights)


def _fp_std(games: list[_Game]) -> float:
    recent = [g.points for g in games[-EWMA_WINDOW:]]
    if len(recent) < STD_MIN_GAMES:
        return NAN
    return sample_stddev(recent, _mean(recent))


def _team_recent_weeks(
    team_week_order: dict[str, list[int]], team: str | None, before_week: int, n: int
) -> list[int]:
    """The team's last ``n`` game weeks strictly before ``before_week``."""

    if team is None:
        return []
    weeks = team_week_order.get(team, [])
    cut = bisect_left(weeks, before_week)
    return weeks[max(0, cut - n) : cut]


def _share_over_weeks(
    games: list[_Game],
    code: str,
    team: str | None,
    team_weeks: dict[tuple[str, int], _TeamWeek],
    field: str,
    window: int,
) -> float:
    """Player stat over last ``window`` active games / team total in those
    same weeks."""

    recent = games[-window:]
    if not recent or team is None:
        return NAN
    denom = 0.0
    for g in recent:
        tw = team_weeks.get((team, g.week))
        if tw is not None:
            denom += getattr(tw, field)
    return _sum_stat(recent, code) / denom if denom > 0 else NAN


def _weekly_share_series(
    games: list[_Game],
    code: str,
    team: str | None,
    team_weeks: dict[tuple[str, int], _TeamWeek],
    field: str,
) -> list[float]:
    if team is None:
        return []
    out: list[float] = []
    for g in games:
        tw = team_weeks.get((team, g.week))
        denom = getattr(tw, field) if tw is not None else 0.0
        if denom > 0:
            out.append(g.stats.get(code, 0.0) / denom)
    return out


def _feature_vector(
    pos: str,
    games: list[_Game],
    *,
    team: str | None,
    week: int,
    opponent: str | None,
    team_weeks: dict[tuple[str, int], _TeamWeek],
    team_week_order: dict[str, list[int]],
    home_teams: dict[int, frozenset[str]],
    defense: _DefenseTables,
    prior_fp_pg: float,
) -> tuple[float, ...]:
    """The spec's shared block + one position block. ``games`` holds the
    player's prior active games only — the week being predicted is never
    in it (walk-forward at fit time, completed weeks at predict time)."""

    if week in home_teams and team is not None:
        home_flag = 1.0 if team in home_teams[week] else 0.0
    else:
        home_flag = NAN

    shared = (
        _fp_ewma(games),
        _fp_std(games),
        _trend_2v3([g.points for g in games]),
        float(min(len(games), EWMA_WINDOW)),
        prior_fp_pg,
        home_flag,
        _stat_ratio(games, "off_snp", "tm_off_snp", 3),
        defense.fpa_index(opponent, pos),
    )

    recent3 = _team_recent_weeks(team_week_order, team, week, 3)
    team3 = [team_weeks[(team, w)] for w in recent3] if team else []

    if pos == "QB":
        team_att = sum(t.pass_att + t.rush_att for t in team3)
        block = (
            _per_game(games, "pass_att", 3),
            _stat_ratio(games, "pass_cmp", "pass_att", 3),
            _stat_ratio(games, "pass_yd", "pass_att", 5),
            _stat_ratio(games, "pass_td", "pass_att", 5),
            _stat_ratio(games, "pass_int", "pass_att", 5),
            _per_game(games, "rush_att", 3),
            _per_game(games, "rush_yd", 3),
            _per_game(games, "rush_rz_att", 5),
            sum(t.pass_att for t in team3) / team_att if team_att > 0 else NAN,
            defense.sack_rate(opponent),
        )
    elif pos == "RB":
        opps = [g.stats.get("rush_att", 0.0) + g.stats.get("rec_tgt", 0.0) for g in games]
        rz5 = games[-5:]
        block = (
            _per_game(games, "rush_att", 3),
            _per_game(games, "rec_tgt", 3),
            _rb_opp_share(games, team, team_weeks, 3),
            (_sum_stat(rz5, "rush_rz_att") + _sum_stat(rz5, "rec_rz_tgt")) / len(rz5)
            if rz5
            else NAN,
            _stat_ratio(games, "rush_yd", "rush_att", 5),
            _stat_ratio(games, "rec_yd", "rec_tgt", 5),
            _trend_2v3(opps),
            _mean([t.rush_att for t in team3]),
            _share_over_weeks(games, "rush_rz_att", team, team_weeks, "rb_rz_att", 5),
            defense.rb_eff_index(opponent),
        )
    else:  # WR / TE share a block per the spec
        qb_att = sum(t.qb_pass_att for t in team3)
        block = (
            _per_game(games, "rec_tgt", 3),
            _share_over_weeks(games, "rec_tgt", team, team_weeks, "targets", 3),
            _share_over_weeks(games, "rec_air_yd", team, team_weeks, "air_yd", 3),
            _stat_ratio(games, "rec_air_yd", "rec_tgt", 5),
            _stat_ratio(games, "rec_yd", "rec_tgt", 5),
            _share_over_weeks(games, "rec_rz_tgt", team, team_weeks, "rz_targets", 5),
            _trend_2v3(_weekly_share_series(games, "rec_tgt", team, team_weeks, "targets")),
            _per_game(games, "rec", 3),
            _mean([t.pass_att for t in team3]),
            sum(t.qb_pass_yd for t in team3) / qb_att if qb_att > 0 else NAN,
        )

    return shared + block


def _rb_opp_share(
    games: list[_Game],
    team: str | None,
    team_weeks: dict[tuple[str, int], _TeamWeek],
    window: int,
) -> float:
    """Backfield opportunity share: (carries + targets) / team RB total."""

    recent = games[-window:]
    if not recent or team is None:
        return NAN
    denom = 0.0
    for g in recent:
        tw = team_weeks.get((team, g.week))
        if tw is not None:
            denom += tw.rb_opps
    player = sum(g.stats.get("rush_att", 0.0) + g.stats.get("rec_tgt", 0.0) for g in recent)
    return player / denom if denom > 0 else NAN


# --------------------------------------------------------------------------
# Walk-forward training.


def _fit_all_positions(
    by_week: dict[int, list[tuple[str, dict[str, float]]]],
    position_of: dict[str, str],
    team_of: dict[str, str],
    team_weeks: dict[tuple[str, int], _TeamWeek],
    team_week_order: dict[str, list[int]],
    schedule: dict[int, dict[str, str]],
    home_teams: dict[int, frozenset[str]],
    prior_season_stats: dict[str, dict[str, float]],
    league_scoring: ScoringSettings,
) -> _ScoringFit:
    """One pass over the season in week order: emit training rows for
    week W from state accumulated over weeks < W, then fold W in."""

    prior_fp_pg: dict[str, float] = {}
    for pid, totals in prior_season_stats.items():
        gp = totals.get("gp", 0.0)
        if gp > 0:
            prior_fp_pg[pid] = weekly_points(totals, league_scoring) / gp

    defense = _DefenseTables()
    games_so_far: dict[str, list[_Game]] = {}
    rows: dict[str, list[tuple[tuple[float, ...], float]]] = {p: [] for p in POSITIONS}

    for week in sorted(by_week):
        week_rows = by_week[week]
        opponents = schedule.get(week, {})

        for pid, stats in week_rows:
            prior_games = games_so_far.get(pid)
            if not prior_games:
                continue
            pos = position_of[pid]
            team = team_of.get(pid)
            feats = _feature_vector(
                pos,
                prior_games,
                team=team,
                week=week,
                opponent=opponents.get(team) if team else None,
                team_weeks=team_weeks,
                team_week_order=team_week_order,
                home_teams=home_teams,
                defense=defense,
                prior_fp_pg=prior_fp_pg.get(pid, NAN),
            )
            rows[pos].append((feats, weekly_points(stats, league_scoring)))

        _fold_week_into_defense(
            defense,
            week_rows,
            position_of,
            team_of,
            opponents,
            games_so_far,
            league_scoring,
        )

        for pid, stats in week_rows:
            games_so_far.setdefault(pid, []).append(
                _Game(week, weekly_points(stats, league_scoring), stats)
            )

    fits: dict[str, _PositionFit] = {}
    for pos, pos_rows in rows.items():
        if len(pos_rows) < MIN_ROWS_PER_POSITION:
            log.info(
                "gbt: %s has %d training rows (<%d); naive fallback",
                pos,
                len(pos_rows),
                MIN_ROWS_PER_POSITION,
            )
            continue
        fits[pos] = _fit_gbt(pos_rows)

    return _ScoringFit(fits=fits, defense=defense, prior_fp_pg=prior_fp_pg)


def _fold_week_into_defense(
    defense: _DefenseTables,
    week_rows: list[tuple[str, dict[str, float]]],
    position_of: dict[str, str],
    team_of: dict[str, str],
    opponents: dict[str, str],
    games_so_far: dict[str, list[_Game]],
    league_scoring: ScoringSettings,
) -> None:
    """Add one completed week to the opponent-strength accumulators.

    The fpa index compares observed points against the same players'
    pre-game ewma, so players without a valid ewma (fewer than 2 prior
    games) are excluded from both sums — including them on one side
    only would bias the ratio.
    """

    seen_def_pos: set[tuple[str, str]] = set()
    for pid, stats in week_rows:
        team = team_of.get(pid)
        opp = opponents.get(team) if team else None
        if opp is None:
            continue
        pos = position_of[pid]

        ewma = _fp_ewma(games_so_far.get(pid, []))
        if not math.isnan(ewma):
            entry = defense.fpa.setdefault((opp, pos), [0.0, 0.0, 0.0])
            entry[0] += weekly_points(stats, league_scoring)
            entry[1] += ewma
            if (opp, pos) not in seen_def_pos:
                entry[2] += 1.0
                seen_def_pos.add((opp, pos))

        if pos == "RB":
            att = stats.get("rush_att", 0.0)
            yd = stats.get("rush_yd", 0.0)
            rb = defense.rb_rush.setdefault(opp, [0.0, 0.0, 0.0])
            rb[0] += yd
            rb[1] += att
            defense.league_rb_yd += yd
            defense.league_rb_att += att
        elif pos == "QB":
            sacks = stats.get("pass_sack", 0.0)
            dropbacks = stats.get("pass_att", 0.0) + sacks
            sk = defense.sacks.setdefault(opp, [0.0, 0.0])
            sk[0] += sacks
            sk[1] += dropbacks
            defense.league_sacks += sacks
            defense.league_dropbacks += dropbacks

    # Count each defense-position matchup week as one game for the RB
    # index too (entry[2] above already handles fpa).
    seen_rb: set[str] = set()
    for pid, _stats in week_rows:
        team = team_of.get(pid)
        opp = opponents.get(team) if team else None
        if opp is None or position_of[pid] != "RB" or opp in seen_rb:
            continue
        rb_entry = defense.rb_rush.get(opp)
        if rb_entry is not None:
            rb_entry[2] += 1.0
            seen_rb.add(opp)


# --------------------------------------------------------------------------
# The regressor: quantile-binned gradient-boosted trees, squared loss.


def _bin_edges(values: list[float]) -> tuple[float, ...]:
    """Up to N_BINS-1 quantile cut points over the non-NaN values."""

    clean = sorted(v for v in values if not math.isnan(v))
    if not clean:
        return ()
    edges: list[float] = []
    for i in range(1, N_BINS):
        edge = clean[min(len(clean) - 1, (i * len(clean)) // N_BINS)]
        if not edges or edge > edges[-1]:
            edges.append(edge)
    return tuple(edges)


def _bin_value(edges: tuple[float, ...], value: float) -> int:
    """NaN -> reserved bin 0; otherwise 1 + index among the cut points."""

    if math.isnan(value):
        return 0
    return 1 + bisect_right(edges, value)


def _fit_gbt(rows: list[tuple[tuple[float, ...], float]]) -> _PositionFit:
    n = len(rows)
    edges = tuple(_bin_edges([feats[i] for feats, _y in rows]) for i in range(N_FEATURES))
    binned = [tuple(_bin_value(edges[i], f) for i, f in enumerate(feats)) for feats, _y in rows]
    y = [target for _feats, target in rows]

    base = sum(y) / n
    preds = [base] * n
    trees: list[tuple[_TreeNode, ...]] = []
    for _round in range(N_TREES):
        residuals = [y[i] - preds[i] for i in range(n)]
        nodes: list[_TreeNode] = []
        _grow(nodes, binned, residuals, list(range(n)), depth=0)
        tree = tuple(nodes)
        trees.append(tree)
        for i in range(n):
            preds[i] += _tree_output_binned(tree, binned[i])

    return _PositionFit(bin_edges=edges, base=base, trees=tuple(trees), n_rows=n)


def _grow(
    nodes: list[_TreeNode],
    binned: list[tuple[int, ...]],
    residuals: list[float],
    idx: list[int],
    depth: int,
) -> int:
    """Grow one node greedily; returns its index in ``nodes``."""

    total = sum(residuals[i] for i in idx)
    count = len(idx)

    best_gain = 0.0
    best_feat = -1
    best_bin = -1
    if depth < MAX_DEPTH and count >= 2 * MIN_LEAF:
        parent_score = total * total / (count + LEAF_LAMBDA)
        max_bin = N_BINS + 1
        for feat in range(N_FEATURES):
            hist_sum = [0.0] * max_bin
            hist_cnt = [0] * max_bin
            for i in idx:
                b = binned[i][feat]
                hist_sum[b] += residuals[i]
                hist_cnt[b] += 1
            left_sum = 0.0
            left_cnt = 0
            for b in range(max_bin - 1):
                left_sum += hist_sum[b]
                left_cnt += hist_cnt[b]
                right_cnt = count - left_cnt
                if left_cnt < MIN_LEAF or right_cnt < MIN_LEAF:
                    continue
                right_sum = total - left_sum
                gain = (
                    left_sum * left_sum / (left_cnt + LEAF_LAMBDA)
                    + right_sum * right_sum / (right_cnt + LEAF_LAMBDA)
                    - parent_score
                )
                if gain > best_gain:
                    best_gain = gain
                    best_feat = feat
                    best_bin = b

    node_index = len(nodes)
    if best_feat < 0:
        value = LEARNING_RATE * total / (count + LEAF_LAMBDA)
        nodes.append(_TreeNode(-1, -1, -1, -1, value))
        return node_index

    nodes.append(_TreeNode(best_feat, best_bin, -1, -1, 0.0))  # patched below
    left_idx = [i for i in idx if binned[i][best_feat] <= best_bin]
    right_idx = [i for i in idx if binned[i][best_feat] > best_bin]
    left = _grow(nodes, binned, residuals, left_idx, depth + 1)
    right = _grow(nodes, binned, residuals, right_idx, depth + 1)
    nodes[node_index] = _TreeNode(best_feat, best_bin, left, right, 0.0)
    return node_index


def _tree_output_binned(tree: tuple[_TreeNode, ...], binned: tuple[int, ...]) -> float:
    node = tree[0]
    while node.feature >= 0:
        nxt = node.left if binned[node.feature] <= node.split_bin else node.right
        node = tree[nxt]
    return node.value


def _predict(fit: _PositionFit, feats: tuple[float, ...]) -> float:
    binned = tuple(_bin_value(fit.bin_edges[i], f) for i, f in enumerate(feats))
    out = fit.base
    for tree in fit.trees:
        out += _tree_output_binned(tree, binned)
    return out
