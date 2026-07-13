"""Pure orchestration: state -> fetch -> validate -> write.

This module has no I/O of its own. It takes an ``HttpClient`` and a
``SnapshotWriter`` by parameter and orchestrates them. That makes the
whole pipeline unit-testable with fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stats_loader import __version__
from stats_loader.clients.http import HttpClient, NotFoundError
from stats_loader.clients.snapshot_writer import SnapshotWriter
from stats_loader.core.manifest import build_manifest
from stats_loader.core.state import FetchPlan, plan_from_state
from stats_loader.providers import sleeper
from stats_loader.types import NflState

SnapshotWriterFactory = Callable[[int], SnapshotWriter]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of a single pipeline run."""

    plan: FetchPlan
    sources: dict[str, str]
    snapshot_path: Path | None  # None for dry-run
    dry_run: bool


def run(
    *,
    http: HttpClient,
    writer_factory: SnapshotWriterFactory | None,
    state_override: NflState | None,
    now: datetime,
    dry_run: bool,
) -> PipelineResult:
    """Execute the loader.

    Parameters
    ----------
    http : HttpClient
        Performs the GETs. Injected so tests can use a fake.
    writer_factory : (season) -> SnapshotWriter, optional
        Called once after state resolution to construct the per-season
        writer. ``None`` only in dry-run mode.
    state_override : NflState | None
        If set, skip the live ``/v1/state/nfl`` call and use these values.
    now : datetime
        Wallclock at start of run. Used for the manifest and to pick the
        snapshot folder date. Injected so tests can pin it.
    dry_run : bool
        If True, do everything except touch the filesystem.
    """

    started_at = now

    state = _resolve_state(http, state_override)
    plan = plan_from_state(state)
    log.info(
        "NFL state: season=%d week=%d completed_through=%d",
        state.season,
        state.week,
        plan.completed_through_week,
    )

    writer: SnapshotWriter | None = (
        writer_factory(plan.season) if writer_factory is not None else None
    )

    sources: dict[str, str] = {}

    # --- players ---
    players_path = "/v1/players/nfl"
    players_payload = sleeper.validate_players(http.get_json(players_path))
    sources["players"] = players_path
    if writer is not None:
        writer.write_artifact("players.json", players_payload)

    # --- season schedule (who plays whom, per week) ---
    # Feeds the context scoring model's opponent-defense feature. Not
    # load-bearing for the naive model, so a 404 is a soft miss like the
    # prior-season bootstrap — warn and continue rather than fail the run.
    schedule_path = f"/schedule/nfl/regular/{plan.season}"
    try:
        schedule_payload = sleeper.validate_schedule(
            http.get_json(schedule_path), label=schedule_path
        )
    except NotFoundError as exc:
        log.warning("Schedule unavailable (%s); skipping.", exc)
    else:
        sources["schedule"] = schedule_path
        if writer is not None:
            writer.write_artifact("schedule.json", schedule_payload)

    # --- past completed weeks: stats + projections ---
    for week in plan.completed_weeks:
        stats_path = f"/v1/stats/nfl/regular/{plan.season}/{week}"
        proj_path = f"/v1/projections/nfl/regular/{plan.season}/{week}"

        stats_payload = sleeper.validate_weekly(
            http.get_json(stats_path),
            label=stats_path,
            allow_empty=False,
        )
        proj_payload = sleeper.validate_weekly(
            http.get_json(proj_path),
            label=proj_path,
            allow_empty=False,
        )

        sources[f"stats_week_{week}"] = stats_path
        sources[f"projections_week_{week}"] = proj_path

        if writer is not None:
            writer.write_artifact(f"stats_week_{week}.json", stats_payload)
            writer.write_artifact(f"projections_week_{week}.json", proj_payload)

    # --- upcoming / in-progress week's projection ---
    if plan.upcoming_week is not None:
        upcoming_path = f"/v1/projections/nfl/regular/{plan.season}/{plan.upcoming_week}"
        upcoming_payload = sleeper.validate_weekly(
            http.get_json(upcoming_path),
            label=upcoming_path,
            allow_empty=True,
        )
        sources[f"projections_week_{plan.upcoming_week}"] = upcoming_path
        if writer is not None:
            writer.write_artifact(
                f"projections_week_{plan.upcoming_week}.json", upcoming_payload
            )

    # --- prior season bootstrap (only when no current-season weeks done) ---
    if plan.bootstrap_prior_season:
        prior_path = f"/v1/stats/nfl/regular/{plan.prior_season}"
        try:
            prior_payload = sleeper.validate_weekly(
                http.get_json(prior_path),
                label=prior_path,
                allow_empty=False,
            )
        except NotFoundError as exc:
            # If Sleeper doesn't have prior-season totals at this path,
            # treat as a soft miss: the decision engine can still operate
            # without variance bootstrapping. Don't fail the whole run.
            log.warning("Prior season bootstrap unavailable (%s); skipping.", exc)
        else:
            sources["stats_prior_season"] = prior_path
            if writer is not None:
                writer.write_artifact("stats_prior_season.json", prior_payload)

    # Build the full source URLs for the manifest (PRD 1.3 shape).
    sources_full = _expand_sources(sources, http)

    finished_at = datetime.now(started_at.tzinfo) if started_at.tzinfo else datetime.now()
    manifest = build_manifest(
        plan=plan,
        sources=sources_full,
        loader_version=__version__,
        started_at=started_at,
        finished_at=finished_at,
    )

    snapshot_path: Path | None = None
    if writer is not None:
        # Manifest is the commit marker; writer renames temp -> final here.
        snapshot_path = writer.commit(manifest.model_dump(mode="json"))

    return PipelineResult(
        plan=plan,
        sources=sources_full,
        snapshot_path=snapshot_path,
        dry_run=dry_run,
    )


def _resolve_state(http: HttpClient, override: NflState | None) -> NflState:
    if override is not None:
        log.info("Using state override: season=%d week=%d", override.season, override.week)
        return override
    return sleeper.validate_state(http.get_json("/v1/state/nfl"))


def _expand_sources(paths: dict[str, str], http: HttpClient) -> dict[str, str]:
    """Turn relative Sleeper paths into full URLs for the manifest.

    We pull the base URL off the http client when we can; otherwise we
    fall back to leaving the path as-is. (Fakes used in unit tests don't
    expose a base.)
    """

    base = getattr(http, "_base_url", "")
    if not base:
        return dict(paths)
    return {k: f"{base}{v}" for k, v in paths.items()}
