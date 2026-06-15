"""FastAPI dependency providers.

Centralised so endpoints don't construct http clients / snapshot readers
inline. Picks the snapshot backend (local filesystem for dev, GCS in
prod) based on ``Settings.snapshot_backend``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Annotated

from decision_engine.clients.http import SleeperHttpClient
from decision_engine.clients.snapshot_reader import (
    CachingSnapshotReader,
    FilesystemSnapshotReader,
    SnapshotReader,
)
from decision_engine.config.settings import SUPPORTED_SCHEMA_VERSION
from fastapi import Depends
from ffdm_app import season_cache
from ffdm_app.types import LiveState

from api.config import Settings, load_settings

PrepareSeason = Callable[[int, LiveState], None]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_http_client(settings: SettingsDep) -> Iterator[SleeperHttpClient]:
    """Per-request Sleeper http client. Closed on response."""

    client = SleeperHttpClient(settings.sleeper_base_url)
    try:
        yield client
    finally:
        client.close()


HttpClientDep = Annotated[SleeperHttpClient, Depends(get_http_client)]


@lru_cache(maxsize=1)
def _build_snapshot_reader() -> CachingSnapshotReader:
    """Process-wide caching reader. Built once per process, shared by
    every request. The inner reader is chosen by backend; both expose
    ``version_for`` so the caching layer can invalidate on upstream
    changes (filesystem mtime; GCS blob generation).
    """

    settings = load_settings()
    if settings.snapshot_backend == "gcs":
        # Imported lazily so the fs backend doesn't pay the
        # google-cloud-storage import cost during local dev.
        from api.snapshot_gcs import GcsSnapshotReader

        assert settings.gcs_bucket is not None  # validated in load_settings
        gcs = GcsSnapshotReader(
            settings.gcs_bucket,
            prefix=settings.gcs_prefix,
            supported_schema_version=SUPPORTED_SCHEMA_VERSION,
        )
        inner: SnapshotReader = gcs
        return CachingSnapshotReader(inner, version_for=gcs.version_for)

    fs = FilesystemSnapshotReader(
        settings.snapshot_root,
        supported_schema_version=SUPPORTED_SCHEMA_VERSION,
    )
    return CachingSnapshotReader(fs, version_for=fs.version_for)


def get_snapshot_reader(settings: SettingsDep) -> CachingSnapshotReader:
    return _build_snapshot_reader()


SnapshotReaderDep = Annotated[CachingSnapshotReader, Depends(get_snapshot_reader)]


@lru_cache(maxsize=1)
def _build_prepare_season() -> PrepareSeason:
    """Strategy for ensuring a snapshot exists before reading.

    Local dev: download via stats-loader if missing/stale (the current
    behaviour). Prod (GCS): validate the season isn't in the future,
    then no-op — the bucket is pre-seeded out-of-band, and the reader
    raises ``SnapshotMissingError`` if anything's actually missing.
    """

    settings = load_settings()
    if settings.snapshot_backend == "gcs":

        def prepare_readonly(season: int, live_state: LiveState) -> None:
            if season > live_state.season:
                raise season_cache.FutureSeasonError(
                    f"season {season} hasn't started — live state is season "
                    f"{live_state.season}"
                )

        return prepare_readonly

    def prepare_local(season: int, live_state: LiveState) -> None:
        season_cache.ensure_season(
            season,
            snapshot_root=settings.snapshot_root,
            sleeper_base_url=settings.sleeper_base_url,
            live_state=live_state,
        )

    return prepare_local


def get_prepare_season(settings: SettingsDep) -> PrepareSeason:
    return _build_prepare_season()


PrepareSeasonDep = Annotated[PrepareSeason, Depends(get_prepare_season)]
