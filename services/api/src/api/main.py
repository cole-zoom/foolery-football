"""FastAPI application factory.

Run locally:

    uv run --project services/api ffdm-api

Or directly with uvicorn:

    uv run --project services/api uvicorn api.main:app --reload
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from decision_engine.clients.http import SleeperHttpClient
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import errors, live_cache
from api.config import load_settings
from api.routers import (
    comparison,
    context,
    decide,
    decisions,
    leagues,
    players,
    state,
)

log = logging.getLogger(__name__)


def _warm_snapshot_cache() -> None:
    """Pre-load the seasons a fresh session will ask for.

    Runs in a daemon thread at startup so the first user request never
    pays the cold snapshot load (GCS fetch + parse). Warms the default
    browsing season and the one before it (the week-1 prior-season
    fallback and the most common season-picker jump). Best-effort: any
    failure is logged and the request path loads lazily as before.
    """

    from ffdm_app.types import LiveState

    from api.deps import _build_snapshot_reader
    from api.routers.decisions import _default_season

    settings = load_settings()
    try:
        reader = _build_snapshot_reader()
        http = SleeperHttpClient(settings.sleeper_base_url)
        try:
            nfl = live_cache.get_state(http)
        finally:
            http.close()
        season = _default_season(LiveState(season=nfl.season, week=nfl.week))
        for s in (season, season - 1):
            try:
                reader.load(s)
                log.info("Warmed snapshot cache for season %d", s)
            except Exception as exc:
                log.warning("Snapshot warmup for season %d failed: %s", s, exc)
    except Exception as exc:
        log.warning("Snapshot warmup skipped: %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    threading.Thread(
        target=_warm_snapshot_cache, name="snapshot-warmup", daemon=True
    ).start()
    yield


def create_app() -> FastAPI:
    settings = load_settings()

    app = FastAPI(
        title="Fantasy Football Decision Maker API",
        version="0.1.0",
        description=(
            "HTTP layer over decision-engine + stats-loader. Wraps the same "
            "`session.decide` the CLI uses, so output matches byte-for-byte."
        ),
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    errors.register(app)

    app.include_router(state.router)
    app.include_router(leagues.router)
    app.include_router(context.router)
    app.include_router(decide.router)
    app.include_router(decisions.router)
    app.include_router(comparison.router)
    app.include_router(players.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
