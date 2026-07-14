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

A cold season load is latency-bound, not bandwidth-bound: ~20 small
objects behind per-request round trips. So ``load`` fetches the manifest
first (it names every other artifact), then downloads the rest in one
concurrent burst — no per-file ``exists()`` HEADs; a 404 on an optional
artifact just means "absent".
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from decision_engine.clients.snapshot_reader import (
    INJURIES_NAME,
    MANIFEST_NAME,
    PLAYERS_NAME,
    PRIOR_SEASON_NAME,
    SCHEDULE_NAME,
    SnapshotMissingError,
    SnapshotSchemaError,
    assemble_snapshot,
)
from decision_engine.types import SnapshotData
from google.api_core.exceptions import NotFound
from google.cloud import storage  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

# Concurrent blob downloads per season load. A season is ~20 objects;
# beyond that the burst is limited by the client's connection pool.
_MAX_DOWNLOAD_WORKERS = 16


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
        location_label = f"gs://{self._bucket_name}/{season_prefix}"

        def download(name: str) -> bytes:
            payload: bytes = self._bucket.blob(
                f"{season_prefix}/{name}"
            ).download_as_bytes()
            return payload

        try:
            manifest_bytes = download(MANIFEST_NAME)
        except NotFound:
            raise SnapshotMissingError(
                f"no snapshot for season {season} at "
                f"gs://{self._bucket_name}/{season_prefix}/; "
                "seed the bucket with `gsutil rsync` or run stats-loader."
            ) from None

        log.info("Reading snapshot %s", location_label)

        # blobs[name]: bytes = downloaded, None = confirmed absent (404).
        blobs: dict[str, bytes | None] = {MANIFEST_NAME: manifest_bytes}

        def fetch(name: str) -> None:
            try:
                blobs[name] = download(name)
            except NotFound:
                blobs[name] = None

        weeks, upcoming = _peek_weeks(manifest_bytes)
        names = [PLAYERS_NAME, PRIOR_SEASON_NAME, SCHEDULE_NAME, INJURIES_NAME]
        names += [f"stats_week_{w}.json" for w in weeks]
        # Projections are optional per week (older snapshots lack them),
        # but has_object() answers from the prefetched set — anything not
        # listed here is invisible to assemble_snapshot, which silently
        # degrades blend to context and disables the availability gate.
        projection_weeks = sorted(set(weeks) | ({upcoming} if upcoming else set()))
        names += [f"projections_week_{w}.json" for w in projection_weeks]

        try:
            with ThreadPoolExecutor(max_workers=_MAX_DOWNLOAD_WORKERS) as pool:
                # list() propagates the first worker exception (transport
                # errors etc. — NotFound is handled inside fetch).
                list(pool.map(fetch, names))
        except Exception as exc:
            raise SnapshotSchemaError(
                f"{location_label}: failed to read snapshot artifacts: {exc}"
            ) from exc

        def load_json(name: str) -> object:
            payload = blobs.get(name)
            if payload is None:
                raise SnapshotSchemaError(
                    f"{location_label}: failed to read {name}: object not found"
                )
            try:
                # Top-level type is checked per artifact in assemble_snapshot
                # (most artifacts are objects; schedule.json is an array).
                return json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SnapshotSchemaError(
                    f"{location_label}/{name}: malformed JSON: {exc}"
                ) from exc

        def has_object(name: str) -> bool:
            return blobs.get(name) is not None

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

        blob = self._bucket.blob(f"{self._prefix}/{season}/{MANIFEST_NAME}")
        try:
            blob.reload(client=self._client)
        except NotFound:
            return None
        generation = blob.generation
        if generation is None:
            return None
        return str(generation)


def _peek_weeks(manifest_bytes: bytes) -> tuple[list[int], int | None]:
    """(weeks_included, upcoming_week_projection) from a raw manifest, tolerantly.

    Only used to know which ``stats_week_<W>.json`` /
    ``projections_week_<W>.json`` blobs to prefetch —
    ``assemble_snapshot`` remains the real validator, so anything
    unparseable here just means "prefetch nothing extra" and the schema
    error surfaces there with its proper message.
    """

    try:
        manifest = json.loads(manifest_bytes)
        weeks_raw = manifest.get("weeks_included") or []
        weeks = [int(w) for w in weeks_raw] if isinstance(weeks_raw, list) else []
        upcoming = manifest.get("upcoming_week_projection")
        return weeks, int(upcoming) if isinstance(upcoming, int) else None
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return [], None
