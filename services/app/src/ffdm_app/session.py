"""High-level session API. Thin wrapper around stats-loader + decision-engine.

This is the layer a UI (the CLI today, a web app tomorrow) talks to. It
does not prompt the user, does not render — it only takes structured
inputs and returns structured outputs. Keep it that way.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from decision_engine.clients.http import SleeperHttpClient as DecideHttpClient
from decision_engine.clients.snapshot_reader import FilesystemSnapshotReader
from decision_engine.config.settings import (
    DEFAULT_SLEEPER_BASE_URL as DECISION_ENGINE_DEFAULT_BASE_URL,
)
from decision_engine.config.settings import SUPPORTED_SCHEMA_VERSION
from decision_engine.core import pipeline as decide_pipeline
from decision_engine.core.pipeline import DecideRequest, DecideResult
from decision_engine.types import NflState, Pool
from stats_loader.clients.http import SleeperHttpClient as LoaderHttpClient
from stats_loader.providers import sleeper as loader_sleeper

from ffdm_app.season_cache import (
    REGULAR_SEASON_LAST_WEEK,
    ensure_season,
    list_cached_seasons,
)
from ffdm_app.types import AppRequest, LiveState, SeasonInfo

log = logging.getLogger(__name__)

DEFAULT_SLEEPER_BASE_URL = DECISION_ENGINE_DEFAULT_BASE_URL


def default_snapshot_root() -> Path:
    """``<repo>/data/seasons`` discovered by walking up from this file.

    Layout: ``<repo>/services/app/src/ffdm_app/session.py`` — so the repo
    root is four parents up (ffdm_app, src, app, services).
    """

    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    return repo_root / "data" / "seasons"


def fetch_live_state(*, sleeper_base_url: str = DEFAULT_SLEEPER_BASE_URL) -> LiveState:
    """One live call to ``/v1/state/nfl``. Cached by the CLI between prompts."""

    with LoaderHttpClient(sleeper_base_url) as http:
        validated = loader_sleeper.validate_state(http.get_json("/v1/state/nfl"))
    return LiveState(season=validated.season, week=validated.week)


def available_seasons(
    *,
    snapshot_root: Path,
    live_state: LiveState,
    history_years: int = 5,
) -> list[SeasonInfo]:
    """Seasons the user can pick from in the CLI.

    Includes ``live_state.season`` down to ``live_state.season -
    history_years``. Each entry records whether it's already cached.
    """

    cached = set(list_cached_seasons(snapshot_root))
    out: list[SeasonInfo] = []
    for season in range(live_state.season, live_state.season - history_years - 1, -1):
        info = SeasonInfo(
            season=season,
            cached=season in cached,
            completed_through_week=_completed_through(snapshot_root, season)
            if season in cached
            else None,
        )
        out.append(info)
    return out


def default_season(live_state: LiveState) -> int:
    """The season the CLI offers as the default.

    During an active regular season (``week >= 2`` means at least one
    completed week of real games) we default to the live season. In the
    offseason (``week`` is 0 or 1 — Sleeper has flipped to the upcoming
    season but no games have been played yet) we default to the most
    recently completed season instead.
    """

    if live_state.week >= 2:
        return live_state.season
    return live_state.season - 1


def default_week_for_season(season: int, *, live_state: LiveState) -> int:
    """The "current week" question reduces to the most recently completed week.

    For a past season this is week 18. For the current season it's
    ``live_state.week - 1``, floor-clamped to 1 (early in the season) and
    capped at 18.
    """

    if season < live_state.season:
        return REGULAR_SEASON_LAST_WEEK
    return max(1, min(REGULAR_SEASON_LAST_WEEK, live_state.week - 1))


def decide(
    request: AppRequest,
    *,
    live_state: LiveState | None = None,
) -> DecideResult:
    """Ensure the cache, then run the decision engine. Returns the raw result.

    The caller (CLI today, web later) renders.
    """

    snapshot_root = request.snapshot_root or default_snapshot_root()
    base_url = request.sleeper_base_url or DEFAULT_SLEEPER_BASE_URL
    state = live_state or fetch_live_state(sleeper_base_url=base_url)

    ensure_season(
        request.season,
        snapshot_root=snapshot_root,
        sleeper_base_url=base_url,
        live_state=state,
    )

    snapshot_reader = FilesystemSnapshotReader(
        snapshot_root,
        supported_schema_version=SUPPORTED_SCHEMA_VERSION,
    )

    # Replay semantics: scoring sees stats through (week - 1). The
    # decision-engine pipeline drives its state through state_override.
    # The pool resolves to the live league at request time — Sleeper's
    # league endpoints return current state regardless of replay week.
    decide_request = DecideRequest(
        user=request.user,
        league_id=request.league_id,
        slot=request.slot,
        risk=request.risk,
        pool=cast(Pool, request.pool),
        limit=request.limit,
        model=request.model,
        prefer_team=request.prefer_team,
        avoid_team=request.avoid_team,
        state_override=NflState(season=request.season, week=request.week),
    )

    with DecideHttpClient(base_url) as http:
        return decide_pipeline.run(
            http=http,
            snapshot_reader=snapshot_reader,
            request=decide_request,
        )


def _completed_through(snapshot_root: Path, season: int) -> int | None:
    import json

    path = snapshot_root / str(season) / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    value = payload.get("completed_through_week")
    return value if isinstance(value, int) else None
