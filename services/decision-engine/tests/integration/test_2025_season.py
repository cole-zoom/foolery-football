"""Replay the full 2025 NFL season — pick the ideal lineup each week.

For each week W in 1..17:

  1. Build a "snapshot as of week W" — same SnapshotData but with
     weekly_stats filtered to weeks < W. The naive scoring model only
     sees data that would have been available before kickoff.
  2. Score every rostered player for that as-of snapshot.
  3. Greedy-assign the highest-scoring eligible player to each
     starting slot in ``league.roster_positions``, picking
     most-restrictive slots first (K/DEF before FLEX/SUPER_FLEX) so the
     flex slot doesn't accidentally swallow a needed RB.
  4. Print the projected lineup and what it actually scored that week.

Two modes:

- ``test_synthetic_2025_season`` — synthesizes a plausible 15-man
  roster from the snapshot's top scorers per position. No Sleeper
  credentials needed. This is what runs in CI / the smoke check.
- ``test_real_team_2025_season`` — uses live Sleeper for a real user +
  league. Set DE_USER and DE_LEAGUE env vars to enable.

Both are opt-in via ``DECISION_ENGINE_INTEGRATION=1``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from decision_engine.clients.http import SleeperHttpClient
from decision_engine.clients.snapshot_reader import (
    FilesystemSnapshotReader,
    SnapshotMissingError,
)
from decision_engine.config.settings import SUPPORTED_SCHEMA_VERSION
from decision_engine.core.eligibility import (
    NON_SELECTABLE_SLOTS,
    SLOT_ELIGIBILITY,
)
from decision_engine.core.league_fetch import fetch_league_context
from decision_engine.core.pipeline import _build_history
from decision_engine.core.scoring import get_model
from decision_engine.types import (
    League,
    LeagueContext,
    Player,
    PlayerScore,
    Roster,
    SleeperUser,
    SnapshotData,
)

SEASON = 2025
REGULAR_SEASON_WEEKS = range(1, 18)

# A standard PPR roster shape. Used by the synthetic mode; real-team
# mode uses whatever ``roster_positions`` Sleeper returns.
STANDARD_ROSTER_POSITIONS: tuple[str, ...] = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF",
    "BN", "BN", "BN", "BN", "BN", "BN",
)
STANDARD_PPR_SCORING: dict[str, float] = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -2.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rec": 1.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "fum_lost": -2.0,
    # Sleeper sometimes pre-computes these per format. We include them
    # so a player line that already has pts_ppr contributes too.
    "pts_ppr": 0.0,
}

pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.environ.get("DECISION_ENGINE_INTEGRATION") == "1"


def _default_snapshot_root() -> Path:
    """``<repo>/data/seasons`` from this file."""

    return Path(__file__).resolve().parents[4] / "data" / "seasons"


@pytest.fixture(scope="module")
def snapshot() -> SnapshotData:
    """Load the {SEASON} snapshot; skip the suite if it isn't present."""

    if not _integration_enabled():
        pytest.skip("set DECISION_ENGINE_INTEGRATION=1 to run integration tests")

    root = Path(
        os.environ.get("DECISION_ENGINE_SNAPSHOT_ROOT") or _default_snapshot_root()
    )
    reader = FilesystemSnapshotReader(
        root, supported_schema_version=SUPPORTED_SCHEMA_VERSION
    )
    try:
        snap = reader.load(SEASON)
    except SnapshotMissingError as exc:
        pytest.skip(f"no {SEASON} snapshot under {root}: {exc}")

    if not snap.weekly_stats:
        pytest.skip(
            f"snapshot has no weekly_stats; need at least weeks 1..17 of {SEASON}."
        )
    return snap


# ---------------------------------------------------------------------------
# Synthetic mode — runs without Sleeper credentials.
# ---------------------------------------------------------------------------


def test_synthetic_2025_season(snapshot: SnapshotData, capsys: pytest.CaptureFixture[str]) -> None:
    """Synthesize a 15-man roster from snapshot top scorers; replay 1..17.

    The "team" is built by picking the highest-scoring player at each
    position based on full-season 2025 totals. This is an
    optimal-by-hindsight team — useful because it gives the engine a
    plausible roster to choose lineups from, not because it's a
    realistic draft.
    """

    scoring = STANDARD_PPR_SCORING
    roster_player_ids = _synthesize_roster(snapshot, scoring)
    league_ctx = _synthesize_league_context(roster_player_ids)

    _replay_season(snapshot, league_ctx, scoring, http=None, season=SEASON)


# ---------------------------------------------------------------------------
# Real-team mode — opt-in, requires Sleeper credentials.
# ---------------------------------------------------------------------------


def test_real_team_2025_season(snapshot: SnapshotData, capsys: pytest.CaptureFixture[str]) -> None:
    """Replay against a real Sleeper user + league.

    Set DE_USER + DE_LEAGUE to enable. The rosters used for each week
    are pulled from ``/v1/league/<id>/matchups/<week>``, which gives
    the historical roster (not the current one).
    """

    user = os.environ.get("DE_USER")
    league_id = os.environ.get("DE_LEAGUE")
    if not user or not league_id:
        pytest.skip("set DE_USER and DE_LEAGUE to enable the real-team replay")

    with SleeperHttpClient("https://api.sleeper.app") as http:
        ctx = fetch_league_context(
            http,
            username=user,
            league_id=league_id,
            season=SEASON,
        )
        _replay_season(
            snapshot,
            ctx,
            ctx.league.scoring_settings,
            http=http,
            season=SEASON,
        )


# ---------------------------------------------------------------------------
# Replay engine — shared between both modes.
# ---------------------------------------------------------------------------


def _replay_season(
    snapshot: SnapshotData,
    ctx: LeagueContext,
    scoring: dict[str, float],
    *,
    http: SleeperHttpClient | None,
    season: int,
) -> None:
    score_model_factory = get_model("naive")

    print()
    print(f"=== 2025 season replay for {ctx.user.username or ctx.user.user_id} ===")
    print(f"League:           {ctx.league.name!r} ({ctx.league.league_id})")
    print(f"Roster positions: {list(ctx.league.roster_positions)}")
    print(f"Scoring:          rec={scoring.get('rec', 0)} "
          f"pass_yd={scoring.get('pass_yd', 0)} "
          f"rush_td={scoring.get('rush_td', 0)} ...")
    print()

    total_projected = 0.0
    total_actual = 0.0

    for week in _weeks_to_replay():
        roster_ids = _roster_for_week(
            ctx,
            http=http,
            league_id=ctx.league.league_id,
            week=week,
        )

        as_of = _snapshot_as_of(snapshot, week)
        score_fn = score_model_factory(as_of)

        scored: dict[str, tuple[Player, PlayerScore]] = {}
        for pid in roster_ids:
            player = snapshot.players.get(pid)
            if player is None:
                continue
            history = _build_history(pid, as_of)
            ps = score_fn(player, history, scoring, risk=0.5)
            scored[pid] = (player, ps)

        lineup = _greedy_lineup(ctx.league.roster_positions, scored)
        actual_stats = snapshot.weekly_stats.get(week, {})

        projected = sum(ps.projected_mean for _, _, ps in lineup if ps is not None)
        actual = sum(
            _points_for(player.player_id, actual_stats, scoring)
            for _, player, _ in lineup
            if player is not None
        )
        total_projected += projected
        total_actual += actual

        print(f"Week {week:>2}   projected {projected:6.1f}   actual {actual:6.1f}")
        for slot, slot_player, slot_score in lineup:
            if slot_player is None or slot_score is None:
                print(f"   {slot:<11} (no eligible player)")
                continue
            actual_pts = _points_for(slot_player.player_id, actual_stats, scoring)
            name = (slot_player.full_name or slot_player.player_id)[:24]
            team = (slot_player.team or "-")[:3]
            pos = (slot_player.position or "-")[:4]
            print(
                f"   {slot:<11} {name:<24} {team:<3} {pos:<4} "
                f"proj {slot_score.projected_mean:5.1f}   actual {actual_pts:5.1f}"
            )
        print()

    print(f"Season totals: projected {total_projected:.1f}   actual {total_actual:.1f}")

    assert total_actual > 0, "expected at least one starter to have scored"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weeks_to_replay() -> list[int]:
    """Honor ``DE_WEEK`` env var if set; otherwise replay the full season.

    ``DE_WEEK=8`` runs only week 8, scoring against weeks 1..7 of the
    snapshot. Useful when you want one week's lineup without sitting
    through 17.
    """

    one = os.environ.get("DE_WEEK")
    if not one:
        return list(REGULAR_SEASON_WEEKS)
    week = int(one)
    if week not in REGULAR_SEASON_WEEKS:
        raise ValueError(f"DE_WEEK={week} not in {list(REGULAR_SEASON_WEEKS)}")
    return [week]


def _snapshot_as_of(snap: SnapshotData, week: int) -> SnapshotData:
    """Filter weekly_stats / weeks_included to weeks STRICTLY before ``week``."""

    weekly = {w: s for w, s in snap.weekly_stats.items() if w < week}
    weeks = tuple(w for w in snap.weeks_included if w < week)
    return snap.model_copy(update={"weekly_stats": weekly, "weeks_included": weeks})


def _greedy_lineup(
    roster_positions: tuple[str, ...],
    scored: dict[str, tuple[Player, PlayerScore]],
) -> list[tuple[str, Player | None, PlayerScore | None]]:
    """Assign players to starting slots, max-score-first, no double-use.

    Most-restrictive slots first so a flex slot doesn't steal a player
    the only-RB slot needed. Within a slot, pick the highest
    ``risk_adjusted_score`` not yet used.
    """

    starting_indices = [
        i for i, s in enumerate(roster_positions) if s.upper() not in NON_SELECTABLE_SLOTS
    ]
    # Sort indices by slot restrictiveness ascending (fewer eligible
    # positions first). Stable on original index so output order is
    # deterministic.
    starting_indices.sort(
        key=lambda i: (
            len(SLOT_ELIGIBILITY.get(roster_positions[i].upper(), frozenset({roster_positions[i].upper()}))),
            i,
        )
    )

    assignments: dict[int, tuple[str, Player | None, PlayerScore | None]] = {}
    used: set[str] = set()
    for idx in starting_indices:
        slot = roster_positions[idx]
        allowed = SLOT_ELIGIBILITY.get(slot.upper())
        if allowed is None:
            # Unknown slot — surface but don't crash the replay.
            assignments[idx] = (slot, None, None)
            continue
        candidates = [
            (pid, player, ps)
            for pid, (player, ps) in scored.items()
            if pid not in used
            and any(pos in allowed for pos in player.fantasy_positions)
        ]
        if not candidates:
            assignments[idx] = (slot, None, None)
            continue
        candidates.sort(key=lambda c: c[2].risk_adjusted_score, reverse=True)
        pid, player, ps = candidates[0]
        used.add(pid)
        assignments[idx] = (slot, player, ps)

    return [assignments[i] for i in sorted(assignments)]


def _points_for(
    pid: str, stats_table: dict[str, dict[str, float]], scoring: dict[str, float]
) -> float:
    stats = stats_table.get(pid) or {}
    return sum(scoring.get(code, 0.0) * v for code, v in stats.items())


def _roster_for_week(
    ctx: LeagueContext,
    *,
    http: SleeperHttpClient | None,
    league_id: str,
    week: int,
) -> list[str]:
    """Player IDs the user rostered going into this week.

    For the real-team mode we pull ``/v1/league/<id>/matchups/<week>``
    so the roster matches what the user actually had then (Sleeper
    keeps it). For the synthetic mode there's no HTTP — the roster is
    fixed across the season.
    """

    if http is None:
        return list(ctx.user_roster.players)

    payload = http.get_json(f"/v1/league/{league_id}/matchups/{week}")
    if not isinstance(payload, list):
        return list(ctx.user_roster.players)
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("roster_id") != ctx.user_roster.roster_id:
            continue
        players = entry.get("players") or []
        ids = [str(p) for p in players if isinstance(p, str)]
        if ids:
            return ids
    return list(ctx.user_roster.players)


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------


# Roster shape for the synthetic team. Five bench slots are fine —
# greedy assignment is over the starting slots only.
SYNTHETIC_ROSTER_BY_POSITION: tuple[tuple[str, int], ...] = (
    ("QB", 2),
    ("RB", 4),
    ("WR", 4),
    ("TE", 2),
    ("K", 1),
    ("DEF", 1),
)


def _synthesize_roster(
    snapshot: SnapshotData, scoring: dict[str, float]
) -> list[str]:
    """Pick the top players per position by full-season 2025 total points."""

    totals: dict[str, float] = {}
    for pid in snapshot.players:
        season_pts = 0.0
        for week_table in snapshot.weekly_stats.values():
            stats = week_table.get(pid)
            if not stats:
                continue
            season_pts += sum(scoring.get(code, 0.0) * v for code, v in stats.items())
        totals[pid] = season_pts

    by_position: dict[str, list[str]] = {}
    for pid, player in snapshot.players.items():
        for pos in player.fantasy_positions:
            by_position.setdefault(pos, []).append(pid)

    roster: list[str] = []
    used: set[str] = set()
    for pos, n in SYNTHETIC_ROSTER_BY_POSITION:
        candidates = sorted(
            (pid for pid in by_position.get(pos, []) if pid not in used),
            key=lambda pid: totals.get(pid, 0.0),
            reverse=True,
        )
        for pid in candidates[:n]:
            roster.append(pid)
            used.add(pid)
    return roster


def _synthesize_league_context(player_ids: list[str]) -> LeagueContext:
    """Build a fake LeagueContext for synthetic-mode replay."""

    user = SleeperUser(user_id="synthetic-user", username="synthetic", display_name="Synthetic")
    league = League(
        league_id="synthetic-league",
        name="Synthetic 2025 PPR",
        season=str(SEASON),
        roster_positions=STANDARD_ROSTER_POSITIONS,
        scoring_settings=STANDARD_PPR_SCORING,
    )
    roster = Roster(
        roster_id=1,
        owner_id=user.user_id,
        players=tuple(player_ids),
        starters=tuple(player_ids[: len(STANDARD_ROSTER_POSITIONS)]),
    )
    return LeagueContext(
        user=user,
        league=league,
        rosters=(roster,),
        user_roster=roster,
    )
