"""CLI entrypoint. Constructs concrete clients; hands them to ``core.pipeline``."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import typer

from stats_loader.clients.http import HttpError, SleeperHttpClient
from stats_loader.clients.snapshot_writer import (
    AtomicSnapshotWriter,
    cleanup_stale_tmp,
)
from stats_loader.config import resolve_settings
from stats_loader.core import pipeline

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _main() -> None:
    """Stats loader: snapshot Sleeper data for the decision engine."""


@app.command()
def update(
    *,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch + validate from Sleeper, but don't write any files.",
    ),
    season: int | None = typer.Option(
        None,
        "--season",
        help="Override the live /v1/state/nfl season (requires --week).",
    ),
    week: int | None = typer.Option(
        None,
        "--week",
        help="Override the live /v1/state/nfl week (requires --season).",
    ),
    snapshot_root: Path | None = typer.Option(
        None,
        "--snapshot-root",
        help="Directory under which the dated snapshot folder is written.",
    ),
    sleeper_base_url: str | None = typer.Option(
        None,
        "--sleeper-base-url",
        help="Override the Sleeper API base URL (for tests/fixtures).",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    ),
) -> None:
    """Snapshot Sleeper data to ``<snapshot-root>/<season>/``."""

    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("stats_loader")

    try:
        settings = resolve_settings(
            snapshot_root=snapshot_root,
            sleeper_base_url=sleeper_base_url,
            dry_run=dry_run,
            season_override=season,
            week_override=week,
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    now = datetime.now().astimezone()

    writer_holder: dict[str, AtomicSnapshotWriter] = {}

    def writer_factory(season: int) -> AtomicSnapshotWriter:
        cleanup_stale_tmp(settings.snapshot_root)
        writer = AtomicSnapshotWriter(settings.snapshot_root, season)
        writer_holder["writer"] = writer
        return writer

    try:
        with SleeperHttpClient(settings.sleeper_base_url) as http:
            result = pipeline.run(
                http=http,
                writer_factory=None if settings.dry_run else writer_factory,
                state_override=settings.state_override,
                now=now,
                dry_run=settings.dry_run,
            )
    except HttpError as exc:
        writer = writer_holder.get("writer")
        if writer is not None:
            writer.abort()
        log.error("Sleeper request failed: %s", exc)
        raise typer.Exit(code=1) from exc
    except Exception:
        writer = writer_holder.get("writer")
        if writer is not None:
            writer.abort()
        raise

    if result.dry_run:
        typer.echo(
            f"dry-run ok: would write {len(result.sources)} artifacts for "
            f"season {result.plan.season}, weeks {list(result.plan.completed_weeks)}"
        )
    else:
        typer.echo(f"wrote {result.snapshot_path}")
