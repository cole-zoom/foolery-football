"""Interactive CLI. Thin: prompts in, renders out, all real work in ``session``.

Designed to be replaced by a web frontend without touching ``session``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import replace
from pathlib import Path

import typer
from decision_engine.clients.http import HttpError
from decision_engine.clients.snapshot_reader import (
    SnapshotMissingError,
    SnapshotSchemaError,
)
from decision_engine.core.eligibility import UnsupportedSlotError
from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.pipeline import DecideResult
from decision_engine.core.scoring import UnknownModelError, display_name
from decision_engine.providers.sleeper import SchemaError as DecideSchemaError
from decision_engine.types import ScoredCandidate
from stats_loader.providers.sleeper import SchemaError as LoaderSchemaError

from ffdm_app import session as session_mod
from ffdm_app.season_cache import FutureSeasonError
from ffdm_app.types import AppRequest, LiveState

app = typer.Typer(no_args_is_help=False, add_completion=False)

DEFAULT_SLOT = "FLEX"
DEFAULT_RISK = 0.5
DEFAULT_POOL = "roster"
SUPPORTED_SLOTS = ("QB", "RB", "WR", "TE", "K", "DEF", "FLEX", "WRRB_FLEX", "WRT_FLEX", "SUPER_FLEX")
SUPPORTED_POOLS = ("roster", "waivers", "both")


@app.command()
def main(
    snapshot_root: Path | None = typer.Option(
        None, "--snapshot-root", help="Override data/seasons/ root."
    ),
    sleeper_base_url: str | None = typer.Option(
        None, "--sleeper-base-url", help="Override the Sleeper API base URL."
    ),
    log_level: str = typer.Option(
        "WARNING", "--log-level", help="DEBUG, INFO, WARNING, ERROR."
    ),
) -> None:
    """Interactive fantasy-football decision maker."""

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    base_url = sleeper_base_url or session_mod.DEFAULT_SLEEPER_BASE_URL
    root = snapshot_root or session_mod.default_snapshot_root()

    typer.echo("Fantasy Football Decision Maker")
    typer.echo("===============================")
    typer.echo("Looking up current NFL state...")
    try:
        live_state = session_mod.fetch_live_state(sleeper_base_url=base_url)
    except (HttpError, LoaderSchemaError) as exc:
        typer.echo(f"error: could not reach Sleeper: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Live state: season {live_state.season}, week {live_state.week}\n")

    league_id = _prompt_nonempty("League ID")
    user = _prompt_nonempty("Username (or user_id)")
    season = _prompt_season(live_state, root)
    slot = _prompt_slot()
    pool = _prompt_pool()
    risk = _prompt_risk()
    week = _prompt_week(season, live_state)

    request = AppRequest(
        league_id=league_id,
        user=user,
        season=season,
        week=week,
        slot=slot,
        risk=risk,
        pool=pool,
        snapshot_root=root,
        sleeper_base_url=base_url,
    )

    _run_and_render(request, live_state)

    # "Week picker" — loop with the same league/user/season.
    while True:
        again = typer.prompt(
            "\nNext? (1-18 week, 's' slot, 'p' pool, 'q' quit)",
            default="q",
            show_default=True,
        ).strip().lower()
        if again in ("q", "quit", "exit", ""):
            break
        if again == "s":
            request = replace(request, slot=_prompt_slot())
            _run_and_render(request, live_state)
            continue
        if again == "p":
            request = replace(request, pool=_prompt_pool())
            _run_and_render(request, live_state)
            continue
        try:
            new_week = int(again)
        except ValueError:
            typer.echo("error: expected 1-18, 's', 'p', or 'q'", err=True)
            continue
        if not 1 <= new_week <= 18:
            typer.echo("error: week must be 1-18", err=True)
            continue
        request = replace(request, week=new_week)
        _run_and_render(request, live_state)


def _run_and_render(request: AppRequest, live_state: LiveState) -> None:
    """Call session.decide, render the result. Print errors but don't exit the loop."""

    target = (request.snapshot_root or session_mod.default_snapshot_root()) / str(request.season)
    if not (target / "manifest.json").is_file():
        typer.echo(f"Season {request.season} not cached — downloading from Sleeper...")
    try:
        result = session_mod.decide(request, live_state=live_state)
    except FutureSeasonError as exc:
        typer.echo(f"error: {exc}", err=True)
        return
    except (UserInputError, UnsupportedSlotError, UnknownModelError) as exc:
        typer.echo(f"error: {exc}", err=True)
        return
    except SnapshotMissingError as exc:
        typer.echo(f"error: {exc}", err=True)
        return
    except SnapshotSchemaError as exc:
        typer.echo(f"error: snapshot unreadable: {exc}", err=True)
        return
    except DecideSchemaError as exc:
        typer.echo(f"error: Sleeper schema mismatch: {exc}", err=True)
        return
    except HttpError as exc:
        typer.echo(f"error: Sleeper request failed: {exc}", err=True)
        return

    _render(result)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _prompt_nonempty(label: str) -> str:
    while True:
        raw: str = typer.prompt(label)
        value = raw.strip()
        if value:
            return value
        typer.echo("error: must not be empty", err=True)


def _prompt_season(live_state: LiveState, snapshot_root: Path) -> int:
    seasons = session_mod.available_seasons(
        snapshot_root=snapshot_root, live_state=live_state
    )
    default = session_mod.default_season(live_state)
    typer.echo("Available seasons:")
    for info in seasons:
        cache_label = "cached" if info.cached else "will download"
        marker = "  <-- default" if info.season == default else ""
        typer.echo(f"  {info.season} ({cache_label}){marker}")
    while True:
        raw: str = typer.prompt("Season", default=str(default), show_default=True)
        raw = raw.strip()
        try:
            season = int(raw)
        except ValueError:
            typer.echo("error: expected an integer year", err=True)
            continue
        if season > live_state.season:
            typer.echo(
                f"error: season {season} hasn't started — latest is {live_state.season}",
                err=True,
            )
            continue
        return season


def _prompt_slot() -> str:
    typer.echo(f"Slots: {', '.join(SUPPORTED_SLOTS)}")
    while True:
        raw: str = typer.prompt("Slot", default=DEFAULT_SLOT, show_default=True)
        normalized = raw.strip().upper()
        if normalized in SUPPORTED_SLOTS:
            return normalized
        typer.echo(f"error: slot must be one of {SUPPORTED_SLOTS}", err=True)


def _prompt_pool() -> str:
    typer.echo(
        "Pool: roster (your players), waivers (free agents), "
        "both (roster + free agents)"
    )
    while True:
        raw: str = typer.prompt("Pool", default=DEFAULT_POOL, show_default=True)
        normalized = raw.strip().lower()
        if normalized in SUPPORTED_POOLS:
            return normalized
        typer.echo(f"error: pool must be one of {SUPPORTED_POOLS}", err=True)


def _prompt_risk() -> float:
    while True:
        raw = typer.prompt(
            "Risk (0.0 safe, 1.0 gamble)",
            default=str(DEFAULT_RISK),
            show_default=True,
        ).strip()
        try:
            risk = float(raw)
        except ValueError:
            typer.echo("error: expected a number 0.0-1.0", err=True)
            continue
        if not 0.0 <= risk <= 1.0:
            typer.echo("error: must be between 0.0 and 1.0", err=True)
            continue
        return risk


def _prompt_week(season: int, live_state: LiveState) -> int:
    default = session_mod.default_week_for_season(season, live_state=live_state)
    while True:
        raw = typer.prompt(
            "Week (1-18, where N means stats through N-1)",
            default=str(default),
            show_default=True,
        ).strip()
        try:
            week = int(raw)
        except ValueError:
            typer.echo("error: expected an integer", err=True)
            continue
        if not 1 <= week <= 18:
            typer.echo("error: week must be 1-18", err=True)
            continue
        return week


# ---------------------------------------------------------------------------
# Rendering — copied from decision_engine.entrypoint and adapted.
# ---------------------------------------------------------------------------

def _render(result: DecideResult) -> None:
    req = result.request
    snap = result.snapshot
    league = result.league_context.league
    user = result.league_context.user
    state = result.state

    rec_weight = league.scoring_settings.get("rec", 0.0)

    sys.stdout.write(
        f"\nSnapshot: {snap.snapshot_dir}  (season {snap.season}, week {state.week})\n"
        f'League:   "{league.name}" ({league.league_id}), '
        f"scoring: {_scoring_summary(rec_weight)} (rec={rec_weight:.1f})\n"
        f"User:     {user.username or req.user} (user_id {user.user_id})\n"
        f"Slot:     {req.slot}  Risk: {req.risk:.2f}  Pool: {req.pool}  "
        f"Model: {display_name(req.model)} ({req.model})\n"
        "\n"
    )

    if not result.candidates:
        sys.stdout.write("(no eligible candidates)\n")
        return

    sys.stdout.write(
        f"{'Rank':>4}  {'Player':<22} {'Team':<4} {'Pos':<4} "
        f"{'Mean':>6} {'Var':>6} {'Score':>7}  Notes\n"
    )
    for rank, cand in enumerate(result.candidates, start=1):
        sys.stdout.write(_format_row(rank, cand))


def _format_row(rank: int, cand: ScoredCandidate) -> str:
    p = cand.player
    name = (p.full_name or p.player_id)[:22]
    team = (p.team or "-")[:4]
    pos = (p.position or "-")[:4]
    notes = _notes_for(cand)
    notes_str = f"  {', '.join(notes)}" if notes else ""
    return (
        f"{rank:>4}  {name:<22} {team:<4} {pos:<4} "
        f"{cand.score.projected_mean:>6.1f} "
        f"{cand.score.projected_variance:>6.1f} "
        f"{cand.final_score:>7.2f}"
        f"{notes_str}\n"
    )


def _notes_for(cand: ScoredCandidate) -> list[str]:
    notes = list(cand.score.notes)
    if cand.player.injury_status:
        notes.append(cand.player.injury_status)
    if cand.preference_note:
        notes.append(cand.preference_note)
    return notes


def _scoring_summary(rec_weight: float) -> str:
    if rec_weight >= 0.99:
        return "PPR"
    if rec_weight >= 0.49:
        return "half-PPR"
    return "standard"
