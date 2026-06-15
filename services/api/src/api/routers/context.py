"""GET /leagues/{league_id}/context — slot grid + starters + bench.

This is what the React app calls right after the user picks a league.
It returns enough info to render the lineup card without further calls.
"""

from __future__ import annotations

from collections import Counter

from decision_engine.core.eligibility import NON_SELECTABLE_SLOTS
from decision_engine.core.league_fetch import fetch_league_context, resolve_state
from decision_engine.types import LeagueContext, Player
from fastapi import APIRouter, Query
from ffdm_app.types import LiveState

from api.deps import (
    HttpClientDep,
    PrepareSeasonDep,
    SettingsDep,
    SnapshotReaderDep,
)
from api.hydrate import player_to_wire
from api.schemas import LeagueContextOut, LeagueSummaryOut, RosterSlotOut

router = APIRouter(tags=["context"])


@router.get("/leagues/{league_id}/context", response_model=LeagueContextOut)
def get_league_context(
    league_id: str,
    user: str,
    http: HttpClientDep,
    snapshot_reader: SnapshotReaderDep,
    prepare_season: PrepareSeasonDep,
    settings: SettingsDep,
    season: int | None = Query(default=None),
) -> LeagueContextOut:
    state = resolve_state(http, None)
    resolved_season = season if season is not None else state.season
    ctx = fetch_league_context(
        http, username=user, league_id=league_id, season=resolved_season
    )

    prepare_season(
        resolved_season,
        LiveState(season=state.season, week=state.week),
    )

    snapshot = snapshot_reader.load(resolved_season)
    players = snapshot.players
    base = settings.headshot_base_url

    starters = list(ctx.user_roster.starters)
    bench_ids = [p for p in ctx.user_roster.players if p not in set(starters)]

    slots = _build_slots(ctx, players, starters, headshot_base=base)
    bench = [
        player_to_wire(players[pid], headshot_base=base)
        for pid in bench_ids
        if pid in players
    ]
    all_roster = [
        player_to_wire(players[pid], headshot_base=base)
        for pid in ctx.user_roster.players
        if pid in players
    ]

    return LeagueContextOut(
        league=LeagueSummaryOut(
            league_id=ctx.league.league_id,
            name=ctx.league.name,
            season=ctx.league.season,
        ),
        user_id=ctx.user.user_id,
        username=ctx.user.username,
        display_name=ctx.user.display_name,
        roster_positions=list(ctx.league.roster_positions),
        slots=slots,
        bench=bench,
        all_roster_players=all_roster,
    )


def _build_slots(
    ctx: LeagueContext,
    players: dict[str, Player],
    starters: list[str],
    *,
    headshot_base: str,
) -> list[RosterSlotOut]:
    """One slot per ``league.roster_positions`` entry, paired with the
    starter Sleeper has assigned to it (index-aligned).

    Repeated slots (RB, RB, BN, BN, ...) get a 1-based suffix in their
    stable id (RB1, RB2) so the React grid can key off it.
    """

    seen: Counter[str] = Counter()
    out: list[RosterSlotOut] = []
    for i, slot in enumerate(ctx.league.roster_positions):
        seen[slot] += 1
        slot_id = f"{slot}{seen[slot]}"

        starter_pid = starters[i] if i < len(starters) else None
        starter_player = (
            player_to_wire(players[starter_pid], headshot_base=headshot_base)
            if starter_pid and starter_pid in players
            else None
        )

        out.append(
            RosterSlotOut(
                slot_id=slot_id,
                slot=slot,
                selectable=slot not in NON_SELECTABLE_SLOTS,
                starter_player=starter_player,
            )
        )
    return out
