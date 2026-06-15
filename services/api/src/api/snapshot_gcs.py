"""GCS-backed snapshot reader.

The bucket mirrors the local layout under a fixed prefix:

    gs://<bucket>/<prefix>/<season>/manifest.json
    gs://<bucket>/<prefix>/<season>/players.json
    gs://<bucket>/<prefix>/<season>/stats_week_<W>.json
    gs://<bucket>/<prefix>/<season>/stats_prior_season.json

Seed the bucket with
``gsutil -m rsync -r data/seasons gs://<bucket>/seasons``.

Sits in the api package — not decision-engine — so the engine stays free
of cloud SDK deps.
"""

from __future__ import annotations

import json
import logging

from decision_engine.clients.snapshot_reader import (
    MANIFEST_NAME,
    SnapshotMissingError,
    SnapshotSchemaError,
    assemble_snapshot,
)
from decision_engine.types import SnapshotData
from google.cloud import storage  # type: ignore[attr-defined]

log = logging.getLogger(__name__)


class GcsSnapshotReader:
    """Loads snapshots from a GCS bucket. Read-only."""

    def __init__(
        self,
        bucket_name: str,
        *,
        supported_schema_version: int,
        prefix: str = "seasons",
        client: storage.Client | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix.strip("/")
        self._supported_schema_version = supported_schema_version
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def load(self, season: int) -> SnapshotData:
        season_prefix = f"{self._prefix}/{season}"
        manifest_blob = self._bucket.blob(f"{season_prefix}/{MANIFEST_NAME}")
        if not manifest_blob.exists(self._client):
            raise SnapshotMissingError(
                f"no snapshot for season {season} at "
                f"gs://{self._bucket_name}/{season_prefix}/; "
                "seed the bucket with `gsutil rsync` or run stats-loader."
            )

        location_label = f"gs://{self._bucket_name}/{season_prefix}"
        log.info("Reading snapshot %s", location_label)

        def load_json(name: str) -> dict[str, object]:
            blob = self._bucket.blob(f"{season_prefix}/{name}")
            try:
                payload = blob.download_as_bytes()
            except Exception as exc:
                # google.api_core.exceptions.NotFound or transport errors.
                raise SnapshotSchemaError(
                    f"{location_label}: failed to read {name}: {exc}"
                ) from exc
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SnapshotSchemaError(
                    f"{location_label}/{name}: malformed JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise SnapshotSchemaError(
                    f"{location_label}/{name}: expected object at top level, "
                    f"got {type(parsed).__name__}"
                )
            return parsed

        def has_object(name: str) -> bool:
            return self._bucket.blob(f"{season_prefix}/{name}").exists(self._client)

        return assemble_snapshot(
            season,
            load_json=load_json,
            has_object=has_object,
            location_label=location_label,
            supported_schema_version=self._supported_schema_version,
        )

    def version_for(self, season: int) -> str | None:
        """Manifest blob generation as the cache-invalidation token.

        A new generation is assigned on every upload, so the in-process
        snapshot cache picks up freshly-uploaded snapshots automatically.
        Returns ``None`` if the manifest doesn't exist — the caller
        delegates to ``load()`` which raises ``SnapshotMissingError``.

        Costs one HEAD-style call per request (~50ms same-region). Cheap
        enough given the snapshot itself is served from in-memory cache.
        """

        from google.api_core.exceptions import NotFound  # type: ignore[import-untyped]

        blob = self._bucket.blob(f"{self._prefix}/{season}/{MANIFEST_NAME}")
        try:
            blob.reload(client=self._client)
        except NotFound:
            return None
        generation = blob.generation
        if generation is None:
            return None
        return str(generation)
