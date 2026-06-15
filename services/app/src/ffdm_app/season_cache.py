"""Per-year cache layer for Sleeper snapshots.

``data/seasons/<year>/`` holds one snapshot per NFL season. Past seasons
are immutable — once present, never re-downloaded. The current season
re-downloads when:

- the cache is missing, OR
- it's older than ``FRESHNESS_SECONDS`` and the live state advanced past
  the cached ``completed_through_week``.

Downloads delegate to ``stats_loader.core.pipeline`` — we don't
re-implement the fetcher.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from stats_loader.clients.http import SleeperHttpClient
from stats_loader.clients.snapshot_writer import (
    AtomicSnapshotWriter,
    cleanup_stale_tmp,
)
from stats_loader.core import pipeline as loader_pipeline
from stats_loader.types import NflState

from ffdm_app.types import LiveState

log = logging.getLogger(__name__)

FRESHNESS_SECONDS = 24 * 60 * 60
REGULAR_SEASON_LAST_WEEK = 18  # NFL regular season runs weeks 1..18 from 2021 onward
POST_SEASON_WEEK = REGULAR_SEASON_LAST_WEEK + 1

# Prefetch dedup: one background download per (snapshot_root, season) per
# process lifetime. Avoids spawning N threads when context/decisions/players
# all fire on the same page load.
_prefetch_started: set[tuple[Path, int]] = set()
_prefetch_lock = threading.Lock()


class FutureSeasonError(ValueError):
    """Asked for a season Sleeper doesn't have yet."""


def ensure_season(
    season: int,
    *,
    snapshot_root: Path,
    sleeper_base_url: str,
    live_state: LiveState,
    prefetch_prior: bool = True,
) -> Path:
    """Ensure ``<snapshot_root>/<season>/`` exists and is usable.

    Returns the path to the season folder.

    When ``prefetch_prior`` is set and the prior season's folder is
    missing, kicks off a daemon thread to download it. The thread runs
    after the foreground download completes so users get the current
    season's results without waiting for an extra ~15s. The prior season
    is needed for week-1 replay scoring (no current-season history yet).
    """

    if season > live_state.season:
        raise FutureSeasonError(
            f"season {season} hasn't started — live state is season "
            f"{live_state.season}"
        )

    target = snapshot_root / str(season)

    if season < live_state.season:
        # Past season: cached forever once present.
        if _has_complete_manifest(target):
            log.info("Using cached snapshot for past season %d", season)
        else:
            log.info("Past season %d not cached; downloading...", season)
            _download(
                season=season,
                week=POST_SEASON_WEEK,
                snapshot_root=snapshot_root,
                sleeper_base_url=sleeper_base_url,
            )
    else:
        # Current season.
        if _is_fresh(target, live_state):
            log.info("Using fresh cached snapshot for current season %d", season)
        else:
            log.info("Current season %d cache stale or missing; refreshing...", season)
            _download(
                season=season,
                week=live_state.week,
                snapshot_root=snapshot_root,
                sleeper_base_url=sleeper_base_url,
            )

    if prefetch_prior:
        _maybe_prefetch_prior(
            season=season,
            snapshot_root=snapshot_root,
            sleeper_base_url=sleeper_base_url,
        )

    return target


def _maybe_prefetch_prior(
    *,
    season: int,
    snapshot_root: Path,
    sleeper_base_url: str,
) -> None:
    """Kick off a background download of ``season - 1`` if it's missing.

    Cheap insurance for the "user clicks week 1" path, where scoring has
    no current-season history and must fall back on the prior season.
    Dedup'd per (root, season) so concurrent endpoints don't race.
    """

    if season <= 1:
        return
    prior_season = season - 1
    prior_dir = snapshot_root / str(prior_season)
    if _has_complete_manifest(prior_dir):
        return

    key = (snapshot_root, prior_season)
    with _prefetch_lock:
        if key in _prefetch_started:
            return
        _prefetch_started.add(key)

    def worker() -> None:
        try:
            log.info("Prefetching prior season %d in background...", prior_season)
            _download(
                season=prior_season,
                week=POST_SEASON_WEEK,
                snapshot_root=snapshot_root,
                sleeper_base_url=sleeper_base_url,
            )
            log.info("Prior season %d prefetch complete.", prior_season)
        except Exception:
            log.exception("Prior season %d prefetch failed", prior_season)
            # Allow a future ensure_season call to retry.
            with _prefetch_lock:
                _prefetch_started.discard(key)

    threading.Thread(
        target=worker,
        name=f"prefetch-season-{prior_season}",
        daemon=True,
    ).start()


def list_cached_seasons(snapshot_root: Path) -> list[int]:
    """Return all season folders present on disk, ascending."""

    if not snapshot_root.is_dir():
        return []
    out: list[int] = []
    for child in snapshot_root.iterdir():
        if child.is_dir() and child.name.isdigit() and _has_complete_manifest(child):
            out.append(int(child.name))
    out.sort()
    return out


def _has_complete_manifest(season_dir: Path) -> bool:
    return (season_dir / "manifest.json").is_file()


def _is_fresh(season_dir: Path, live_state: LiveState) -> bool:
    """Cache for the current season is fresh iff:

    - manifest exists,
    - completed_through_week >= what the live state implies, AND
    - snapshot_finished_at is within ``FRESHNESS_SECONDS``.

    Live state's ``week`` is the *upcoming* week, so completed games are
    weeks 1..(week-1). Once the regular season is done, ``week`` stays
    at ``POST_SEASON_WEEK`` and ``completed_through_week`` settles at
    ``REGULAR_SEASON_LAST_WEEK``.
    """

    if not _has_complete_manifest(season_dir):
        return False
    try:
        manifest = json.loads((season_dir / "manifest.json").read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not parse %s/manifest.json: %s", season_dir, exc)
        return False

    cached_through = manifest.get("completed_through_week", 0)
    expected_through = max(0, min(live_state.week - 1, REGULAR_SEASON_LAST_WEEK))
    if not isinstance(cached_through, int) or cached_through < expected_through:
        return False

    finished_at_raw = manifest.get("snapshot_finished_at")
    if not isinstance(finished_at_raw, str):
        return False
    try:
        finished_at = datetime.fromisoformat(finished_at_raw)
    except ValueError:
        return False
    age = datetime.now(finished_at.tzinfo) - finished_at
    return age <= timedelta(seconds=FRESHNESS_SECONDS)


def _download(
    *,
    season: int,
    week: int,
    snapshot_root: Path,
    sleeper_base_url: str,
) -> None:
    """Run the stats-loader pipeline with a season/week override."""

    cleanup_stale_tmp(snapshot_root)

    def factory(resolved_season: int) -> AtomicSnapshotWriter:
        return AtomicSnapshotWriter(snapshot_root, resolved_season)

    now = datetime.now().astimezone()
    with SleeperHttpClient(sleeper_base_url) as http:
        loader_pipeline.run(
            http=http,
            writer_factory=factory,
            state_override=NflState(season=season, week=week),
            now=now,
            dry_run=False,
        )
