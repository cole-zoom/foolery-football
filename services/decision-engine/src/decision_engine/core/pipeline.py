"""End-to-end orchestration: snapshot -> league fetch -> score -> rank.

Pure. Accepts the http client + snapshot reader by parameter so it's
unit-testable with fakes. The CLI layer constructs the concrete
clients and hands them in.

This is the library-mode entrypoint per PRD 2.3 ("Library mode"). A
future web UI imports ``run`` directly and never touches the CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from decision_engine.clients.http import HttpClient
from decision_engine.clients.snapshot_reader import SnapshotMissingError, SnapshotReader
from decision_engine.core.eligibility import player_eligible_for_slot
from decision_engine.core.league_fetch import (
    fetch_league_context,
    resolve_state,
)
from decision_engine.core.scoring import build_score_fn
from decision_engine.types import (
    LeagueContext,
    NflState,
    Player,
    Pool,
    ScoredCandidate,
    SnapshotData,
    WeeklyStats,
)

log = logging.getLogger(__name__)

PREFER_TEAM_MULTIPLIER = 1.10
AVOID_TEAM_MULTIPLIER = 0.90


@dataclass(frozen=True, slots=True)
class DecideRequest:
    """All inputs to one decide invocation.

    ``exclude_player_ids`` lets a caller filter players out of the
    candidate pool *before* eligibility/scoring. The lineup-grid endpoint
    uses it to prevent the same player from being recommended into
    multiple slots (e.g. the best WR landing in WR1, WR2, *and* FLEX).
    Defaults to ``None`` so single-slot ``/decide`` calls are unaffected.
    """

    user: str
    league_id: str
    slot: str
    risk: float
    pool: Pool
    limit: int
    model: str
    prefer_team: str | None
    avoid_team: str | None
    state_override: NflState | None
    exclude_player_ids: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class DecideResult:
    """Pipeline output. Used by the CLI for rendering."""

    snapshot: SnapshotData
    league_context: LeagueContext
    state: NflState
    request: DecideRequest
    candidates: tuple[ScoredCandidate, ...]
    # True when the requested week has no current-season history and the
    # scoring model fell back to the prior season's snapshot. UIs use this
    # to show a "using prior season averages" banner.
    using_prior_season: bool = False
    prior_season: int | None = None


def run(
    *,
    http: HttpClient,
    snapshot_reader: SnapshotReader,
    request: DecideRequest,
    snapshot: SnapshotData | None = None,
    league_context: LeagueContext | None = None,
    score_cache: dict[str, ScoredCandidate] | None = None,
) -> DecideResult:
    """Execute the decide pipeline. Returns ranked candidates.

    The optional ``snapshot`` and ``league_context`` parameters let callers
    that already loaded these (e.g. the ``/decisions`` router scoring
    every slot in one request) skip the duplicated I/O. Pass the *raw*
    untrimmed snapshot — replay trimming still happens here so the trim
    contract stays in one place.

    ``score_cache`` shares scored candidates across ``run`` calls: a
    player's score depends on the week, model, risk, and team
    preferences — never on the slot — so a caller looping over slots
    (again the ``/decisions`` router; a WR pool is mostly the FLEX pool)
    can pass one dict and score each player once instead of once per
    eligible slot. Only valid across calls with identical request knobs
    (everything but ``slot``/``exclude_player_ids``); pass a fresh dict
    per user-facing request.
    """

    state = resolve_state(http, request.state_override)
    if snapshot is None:
        snapshot = snapshot_reader.load(state.season)
    # Replay semantics: scoring sees only stats strictly before
    # ``state.week`` — for week N, the model has data from weeks 1..(N-1).
    snapshot = _snapshot_as_of(snapshot, state.week)
    log.info(
        "Loaded snapshot %s (season=%d weeks=%s)",
        snapshot.snapshot_dir,
        snapshot.season,
        list(snapshot.weeks_included),
    )

    # Week-1 fallback: no current-season weeks in the trimmed snapshot
    # means scoring has nothing to look at. Pull last year's snapshot as a
    # substitute. UI banners surface this via ``using_prior_season``.
    prior_snapshot: SnapshotData | None = None
    if not snapshot.weekly_stats:
        try:
            prior_snapshot = snapshot_reader.load(state.season - 1)
            log.info(
                "Week %d has no current-season history; falling back to season %d.",
                state.week,
                state.season - 1,
            )
        except SnapshotMissingError:
            log.info(
                "Week %d has no current-season history and prior season %d "
                "is not cached locally — scoring will return baseline.",
                state.week,
                state.season - 1,
            )

    if league_context is None:
        league_context = fetch_league_context(
            http,
            username=request.user,
            league_id=request.league_id,
            season=state.season,
        )

    # Cached per (model, trimmed snapshot): repeat requests that only
    # change preferences (risk, bias, pool) skip the factory precompute.
    score_fn = build_score_fn(request.model, snapshot)

    pool_player_ids = _build_pool(league_context, snapshot, request.pool)
    excluded = request.exclude_player_ids or frozenset()
    # Bye filter: the season schedule is known upfront, so a player whose
    # team has no week-``state.week`` game can only score zero. Applies
    # to live recommendations and replays alike.
    week_games = snapshot.schedule.get(state.week) or None
    eligible: list[Player] = [
        snapshot.players[pid]
        for pid in pool_player_ids
        if pid not in excluded
        and pid in snapshot.players
        and player_eligible_for_slot(snapshot.players[pid], request.slot)
        and _plays_in_week(snapshot.players[pid], week_games)
    ]
    log.info(
        "Pool=%s slot=%s -> %d eligible candidates (of %d pooled)",
        request.pool,
        request.slot,
        len(eligible),
        len(pool_player_ids),
    )

    user_roster_ids = set(league_context.user_roster.players)

    scored: list[ScoredCandidate] = []
    for player in eligible:
        if score_cache is not None:
            cached = score_cache.get(player.player_id)
            if cached is not None:
                scored.append(cached)
                continue
        history = _build_history(player.player_id, snapshot, prior_snapshot)
        score = score_fn(
            player,
            history,
            league_context.league.scoring_settings,
            request.risk,
        )
        final, pref_note = _apply_team_preferences(
            score.risk_adjusted_score,
            player.team,
            request.prefer_team,
            request.avoid_team,
        )
        candidate = ScoredCandidate(
            player=player,
            score=score,
            final_score=final,
            preference_note=pref_note,
            on_user_roster=player.player_id in user_roster_ids,
        )
        scored.append(candidate)
        if score_cache is not None:
            score_cache[player.player_id] = candidate

    scored.sort(key=lambda c: c.final_score, reverse=True)
    top = tuple(scored[: request.limit])

    return DecideResult(
        snapshot=snapshot,
        league_context=league_context,
        state=state,
        using_prior_season=prior_snapshot is not None,
        prior_season=prior_snapshot.season if prior_snapshot is not None else None,
        request=request,
        candidates=top,
    )


def _build_pool(
    ctx: LeagueContext,
    snapshot: SnapshotData,
    pool: Pool,
) -> list[str]:
    """Resolve which player_ids to consider, per ``--pool``."""

    if pool == "roster":
        return list(ctx.user_roster.players)
    if pool == "waivers":
        rostered = ctx.all_rostered_player_ids
        return [pid for pid in snapshot.players if pid not in rostered]
    # both
    rostered_others = ctx.all_rostered_player_ids - set(ctx.user_roster.players)
    return [pid for pid in snapshot.players if pid not in rostered_others]


def _plays_in_week(player: Player, week_games: dict[str, str] | None) -> bool:
    """False only when the schedule positively shows a bye.

    ``week_games`` is the snapshot schedule's team -> opponent map for
    the target week, or None when the schedule can't say (pre-schedule
    snapshot, or a week outside the regular season). Unknown weeks and
    team-less players (free agents mid-move) are kept — quarantine over
    drop, same as everywhere else.
    """

    if week_games is None or player.team is None:
        return True
    return player.team in week_games


def _snapshot_as_of(snapshot: SnapshotData, week: int) -> SnapshotData:
    """Trim weekly_stats / weeks_included to weeks strictly before ``week``.

    Mirrors the replay test fixture (see ``test_2025_season._snapshot_as_of``).
    Predicting week N means the model has only seen completed weeks 1..(N-1).
    """

    weekly = {w: s for w, s in snapshot.weekly_stats.items() if w < week}
    weeks = tuple(w for w in snapshot.weeks_included if w < week)
    return snapshot.model_copy(update={"weekly_stats": weekly, "weeks_included": weeks})


def _build_history(
    player_id: str,
    snapshot: SnapshotData,
    prior_snapshot: SnapshotData | None = None,
) -> list[WeeklyStats]:
    """Assemble per-week stats for one player.

    Current-season weeks come first. When the (already-trimmed) snapshot
    has no current-season weeks AND a full prior-season snapshot was
    handed in, use the prior season's per-week stats instead. This is
    the "week 1 uses last year" path. Otherwise we still honour the
    legacy bootstrap blob (``stats_prior_season.json``) by synthesising a
    single per-game row from the season totals — same trick the
    position-prior fallback uses.
    """

    out: list[WeeklyStats] = []
    for week, table in snapshot.weekly_stats.items():
        stats = table.get(player_id)
        if stats:
            out.append(WeeklyStats(season=snapshot.season, week=week, stats=stats))

    if out:
        return out

    if prior_snapshot is not None:
        for week, table in prior_snapshot.weekly_stats.items():
            stats = table.get(player_id)
            if stats:
                out.append(
                    WeeklyStats(season=prior_snapshot.season, week=week, stats=stats)
                )
        if out:
            return out

    prior_stats = snapshot.prior_season_stats.get(player_id)
    if prior_stats:
        gp = prior_stats.get("gp", 0.0)
        if gp > 0:
            per_game = {k: v / gp for k, v in prior_stats.items() if k != "gp"}
            out.append(
                WeeklyStats(
                    season=snapshot.season - 1,
                    week=0,
                    stats=per_game,
                )
            )
    return out


def _apply_team_preferences(
    base_score: float,
    player_team: str | None,
    prefer_team: str | None,
    avoid_team: str | None,
) -> tuple[float, str | None]:
    """Apply ±10% team preference multipliers. Returns (score, note)."""

    if player_team is None:
        return base_score, None
    if prefer_team and player_team == prefer_team:
        return base_score * PREFER_TEAM_MULTIPLIER, f"+10% {prefer_team} preference"
    if avoid_team and player_team == avoid_team:
        return base_score * AVOID_TEAM_MULTIPLIER, f"-10% {avoid_team} aversion"
    return base_score, None
