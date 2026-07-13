"""FastAPI application factory.

Run locally:

    uv run --project services/api ffdm-api

Or directly with uvicorn:

    uv run --project services/api uvicorn api.main:app --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import errors
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


def create_app() -> FastAPI:
    settings = load_settings()

    app = FastAPI(
        title="Fantasy Football Decision Maker API",
        version="0.1.0",
        description=(
            "HTTP layer over decision-engine + stats-loader. Wraps the same "
            "`session.decide` the CLI uses, so output matches byte-for-byte."
        ),
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
