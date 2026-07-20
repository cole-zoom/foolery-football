"""CLI entrypoint. Constructs concrete clients; hands them to ``core.pipeline``.

PRD 2.3 defines the flags, output table, and exit-code contract:

- ``0`` — printed a ranked list (length 0 is fine if eligible pool was empty).
- ``1`` — user input validation (unknown user, league mismatch, bad slot, bad risk).
- ``2`` — runtime (Sleeper API down, snapshot missing, schema mismatch).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from decision_engine.clients.http import HttpError, SleeperHttpClient
from decision_engine.clients.snapshot_reader import (
    FilesystemSnapshotReader,
    SnapshotMissingError,
    SnapshotSchemaError,
)
from decision_engine.config import resolve_settings
from decision_engine.config.settings import (
    DEFAULT_LIMIT,
    DEFAULT_MODEL,
    DEFAULT_POOL,
    DEFAULT_RISK,
    SUPPORTED_SCHEMA_VERSION,
)
from decision_engine.core import pipeline
from decision_engine.core.eligibility import UnsupportedSlotError
from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.pipeline import DecideRequest, DecideResult
from decision_engine.core.scoring import UnknownModelError, display_name
from decision_engine.providers.sleeper import SchemaError
from decision_engine.types import NflState, Pool, ScoredCandidate

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def decide(
    *,
    user: str = typer.Option(..., "--user", help="Sleeper username."),
    league: str = typer.Option(..., "--league", help="Sleeper league ID."),
    slot: str = typer.Option(..., "--slot", help="Slot to fill (QB/RB/WR/TE/FLEX/...)."),
    risk: float = typer.Option(
        DEFAULT_RISK,
        "--risk",
        help="0.0 = max safety, 1.0 = max gamble. Default 0.5.",
    ),
    prefer_team: str | None = typer.Option(
        None, "--prefer-team", help="NFL team code to boost by 10%."
    ),
    avoid_team: str | None = typer.Option(
        None, "--avoid-team", help="NFL team code to penalise by 10%."
    ),
    pool: Pool = typer.Option(
        DEFAULT_POOL,
        "--pool",
        help="roster | waivers | both.",
    ),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", help="Max rows printed."),
    season: int | None = typer.Option(
        None, "--season", help="Override /v1/state/nfl season (requires --week)."
    ),
    week: int | None = typer.Option(
        None, "--week", help="Override /v1/state/nfl week (requires --season)."
    ),
    model: str = typer.Option(
        DEFAULT_MODEL,
        "--model",
        help="Scoring model registry key: naive | context | gbt | scratch | "
        "blend (production, = 'Projection Forecast'). See DISPLAY_NAMES.",
    ),
    snapshot_root: Path | None = typer.Option(
        None, "--snapshot-root", help="Override data/seasons/ root."
    ),
    sleeper_base_url: str | None = typer.Option(
        None, "--sleeper-base-url", help="Override the Sleeper API base URL."
    ),
    log_level: str = typer.Option(
        "WARNING",
        "--log-level",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    ),
) -> None:
    """Score the user's roster (or waivers) for ``--slot`` and print ranked."""

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("decision_engine")

    try:
        settings = resolve_settings(
            user=user,
            league_id=league,
            slot=slot,
            risk=risk,
            pool=pool,
            limit=limit,
            model=model,
            prefer_team=prefer_team,
            avoid_team=avoid_team,
            season=season,
            week=week,
            snapshot_root=snapshot_root,
            sleeper_base_url=sleeper_base_url,
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if (settings.season_override is None) != (settings.week_override is None):
        typer.echo("error: --season and --week must be supplied together", err=True)
        raise typer.Exit(code=1)
    state_override: NflState | None = None
    if settings.season_override is not None and settings.week_override is not None:
        state_override = NflState(
            season=settings.season_override, week=settings.week_override
        )

    snapshot_reader = FilesystemSnapshotReader(
        settings.snapshot_root,
        supported_schema_version=SUPPORTED_SCHEMA_VERSION,
    )

    request = DecideRequest(
        user=settings.user,
        league_id=settings.league_id,
        slot=settings.slot,
        risk=settings.risk,
        pool=settings.pool,
        limit=settings.limit,
        model=settings.model,
        prefer_team=settings.prefer_team,
        avoid_team=settings.avoid_team,
        state_override=state_override,
    )

    try:
        with SleeperHttpClient(settings.sleeper_base_url) as http:
            result = pipeline.run(
                http=http,
                snapshot_reader=snapshot_reader,
                request=request,
            )
    except UnsupportedSlotError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except UnknownModelError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except UserInputError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SnapshotMissingError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except SnapshotSchemaError as exc:
        typer.echo(f"error: snapshot unreadable: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except SchemaError as exc:
        typer.echo(f"error: Sleeper schema mismatch: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except HttpError as exc:
        log.error("Sleeper request failed: %s", exc)
        typer.echo(f"error: Sleeper request failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    _render(result)


def _render(result: DecideResult) -> None:
    """Print the PRD 2.3 table to stdout."""

    req = result.request
    snap = result.snapshot
    league = result.league_context.league
    user = result.league_context.user
    state = result.state

    rec_weight = league.scoring_settings.get("rec", 0.0)
    scoring_summary = _scoring_summary(rec_weight)

    sys.stdout.write(
        f"Snapshot: {snap.snapshot_dir}  (season {snap.season}, week {state.week})\n"
        f'League:   "{league.name}" ({league.league_id}), '
        f"scoring: {scoring_summary} (rec={rec_weight:.1f})\n"
        f"User:     {user.username or req.user} (user_id {user.user_id})\n"
        f"Slot:     {req.slot}  Risk: {req.risk:.2f}  Pool: {req.pool}  "
        f"Model: {display_name(req.model)} ({req.model})\n"
        "\n"
    )

    if not result.candidates:
        sys.stdout.write("(no eligible candidates)\n")
        return

    header = f"{'Rank':>4}  {'Player':<22} {'Team':<4} {'Pos':<4} " \
             f"{'Mean':>6} {'Var':>6} {'Score':>7}  Notes\n"
    sys.stdout.write(header)
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
