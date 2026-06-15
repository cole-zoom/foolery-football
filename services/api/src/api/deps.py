"""FastAPI dependency providers.

Centralised so endpoints don't construct http clients / snapshot readers
inline. Lets us swap the snapshot backend (GCS) by changing one place.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from decision_engine.clients.http import SleeperHttpClient
from decision_engine.clients.snapshot_reader import (
    CachingSnapshotReader,
    FilesystemSnapshotReader,
)
from decision_engine.config.settings import SUPPORTED_SCHEMA_VERSION
from fastapi import Depends

from api.config import Settings, load_settings


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
    """Process-wide caching reader. Snapshot files are immutable until
    overwritten on disk (mtime-invalidated), so a single shared instance
    saves N requests × ~200ms of JSON parsing per season.
    """

    settings = load_settings()
    fs = FilesystemSnapshotReader(
        settings.snapshot_root,
        supported_schema_version=SUPPORTED_SCHEMA_VERSION,
    )
    return CachingSnapshotReader(fs, settings.snapshot_root)


def get_snapshot_reader(settings: SettingsDep) -> CachingSnapshotReader:
    return _build_snapshot_reader()


SnapshotReaderDep = Annotated[CachingSnapshotReader, Depends(get_snapshot_reader)]
