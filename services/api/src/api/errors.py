"""Map domain exceptions onto HTTP responses.

Keeps router code clean — they raise the domain exception, FastAPI
turns it into the right status.
"""

from __future__ import annotations

from decision_engine.clients.http import HttpError, NotFoundError
from decision_engine.clients.snapshot_reader import (
    SnapshotMissingError,
    SnapshotSchemaError,
)
from decision_engine.core.eligibility import UnsupportedSlotError
from decision_engine.core.league_fetch import UserInputError
from decision_engine.core.scoring import UnknownModelError
from decision_engine.providers.sleeper import SchemaError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ffdm_app.season_cache import FutureSeasonError


def _json(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def register(app: FastAPI) -> None:
    @app.exception_handler(UserInputError)
    async def _user_input(_req: Request, exc: UserInputError) -> JSONResponse:
        return _json(400, str(exc))

    @app.exception_handler(UnsupportedSlotError)
    async def _bad_slot(_req: Request, exc: UnsupportedSlotError) -> JSONResponse:
        return _json(400, str(exc))

    @app.exception_handler(UnknownModelError)
    async def _bad_model(_req: Request, exc: UnknownModelError) -> JSONResponse:
        return _json(400, str(exc))

    @app.exception_handler(FutureSeasonError)
    async def _future_season(_req: Request, exc: FutureSeasonError) -> JSONResponse:
        return _json(400, str(exc))

    @app.exception_handler(SnapshotMissingError)
    async def _missing_snapshot(
        _req: Request, exc: SnapshotMissingError
    ) -> JSONResponse:
        return _json(503, str(exc))

    @app.exception_handler(SnapshotSchemaError)
    async def _snapshot_schema(
        _req: Request, exc: SnapshotSchemaError
    ) -> JSONResponse:
        return _json(500, f"snapshot schema error: {exc}")

    @app.exception_handler(SchemaError)
    async def _sleeper_schema(_req: Request, exc: SchemaError) -> JSONResponse:
        return _json(502, f"upstream sleeper schema error: {exc}")

    @app.exception_handler(NotFoundError)
    async def _not_found(_req: Request, exc: NotFoundError) -> JSONResponse:
        return _json(404, str(exc))

    @app.exception_handler(HttpError)
    async def _upstream(_req: Request, exc: HttpError) -> JSONResponse:
        return _json(502, f"upstream sleeper error: {exc}")
