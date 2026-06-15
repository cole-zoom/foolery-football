"""Resolve runtime settings from CLI flags + environment.

Settings is a frozen dataclass — once resolved it never mutates. The
entrypoint builds one and passes it down into ``core.pipeline.run``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from stats_loader.types import NflState

DEFAULT_SLEEPER_BASE_URL = "https://api.sleeper.app"


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for a single ``stats-loader update`` run."""

    snapshot_root: Path
    sleeper_base_url: str
    dry_run: bool
    state_override: NflState | None


def resolve_settings(
    *,
    snapshot_root: Path | None,
    sleeper_base_url: str | None,
    dry_run: bool,
    season_override: int | None,
    week_override: int | None,
) -> Settings:
    """Build a Settings from CLI inputs and env, with sensible defaults.

    - ``snapshot_root`` defaults to ``<repo_root>/data/snapshots`` discovered
      by walking up from this file. CLI flag wins, then
      ``STATS_LOADER_SNAPSHOT_ROOT``, then the default.
    - ``sleeper_base_url`` defaults to the production Sleeper base.
      ``STATS_LOADER_SLEEPER_BASE_URL`` overrides for tests.
    - ``season_override`` and ``week_override`` must both be set together;
      if so, they replace the live ``/v1/state/nfl`` lookup.
    """

    if (season_override is None) != (week_override is None):
        raise ValueError("--season and --week must be supplied together")

    state_override = (
        NflState(season=season_override, week=week_override)
        if season_override is not None and week_override is not None
        else None
    )

    resolved_root = (
        snapshot_root
        or _env_path("STATS_LOADER_SNAPSHOT_ROOT")
        or _default_snapshot_root()
    )
    resolved_base = (
        sleeper_base_url
        or os.environ.get("STATS_LOADER_SLEEPER_BASE_URL")
        or DEFAULT_SLEEPER_BASE_URL
    )

    return Settings(
        snapshot_root=resolved_root,
        sleeper_base_url=resolved_base.rstrip("/"),
        dry_run=dry_run,
        state_override=state_override,
    )


def _env_path(key: str) -> Path | None:
    raw = os.environ.get(key)
    return Path(raw).expanduser() if raw else None


def _default_snapshot_root() -> Path:
    """Locate ``<repo>/data/seasons`` by walking up from this file.

    Repo layout: ``<repo>/services/stats-loader/src/stats_loader/config/settings.py``
    so the repo root is six parents up (config, stats_loader, src,
    stats-loader, services, <repo>).
    """

    here = Path(__file__).resolve()
    repo_root = here.parents[5]
    return repo_root / "data" / "seasons"
