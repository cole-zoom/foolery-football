"""Read a per-season snapshot folder into memory.

Concrete I/O. Pure ``core`` accepts the in-memory result via a protocol
— see ``decision_engine.core.pipeline``.

Snapshot format is the contract in
``docs/product-specs/milestone-1/1.3-local-storage-layout.md``. Layout:

- ``<root>/<season>/manifest.json`` — required. Carries
  ``schema_version``, ``season``, ``weeks_included``,
  ``upcoming_week_projection``.
- ``<root>/<season>/players.json`` — required. Sleeper
  ``/v1/players/nfl`` payload.
- ``<root>/<season>/stats_week_<W>.json`` — one per completed week
  (``weeks_included``).
- ``<root>/<season>/stats_prior_season.json`` — present iff
  ``prior_season_bootstrapped`` is true.

Projection files are written by the loader but the naive scoring model
does not consume them. They're still part of the snapshot contract.

The ``FilesystemSnapshotReader`` lives here; the GCS-backed equivalent
lives in the api package so this package stays free of cloud SDKs.
Both delegate to ``assemble_snapshot`` for parsing.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol

from decision_engine.types import Player, SnapshotData

log = logging.getLogger(__name__)

MANIFEST_NAME: Final[str] = "manifest.json"
PLAYERS_NAME: Final[str] = "players.json"
PRIOR_SEASON_NAME: Final[str] = "stats_prior_season.json"


class SnapshotReader(Protocol):
    """Protocol consumed by ``core.pipeline``."""

    def load(self, season: int) -> SnapshotData: ...


class CachingSnapshotReader:
    """In-memory LRU cache around any ``SnapshotReader``.

    Snapshot files are large (~5MB players.json + per-week stats, ~50-90MB
    per season as parsed Python objects) and fully immutable until the
    loader overwrites them. Re-parsing them per request — let alone per
    slot, per prefetched week — is a measurable bottleneck under fan-out
    load. Capped at ``max_seasons`` so we don't OOM after a user pages
    through history.

    Keyed by season, invalidated by an opaque ``version_for(season)``
    token. Local backends use the manifest mtime; the GCS backend uses
    the manifest blob's generation.
    """

    def __init__(
        self,
        inner: SnapshotReader,
        version_for: Callable[[int], str | None],
        *,
        max_seasons: int = 5,
    ) -> None:
        self._inner = inner
        self._version_for = version_for
        self._max_seasons = max_seasons
        self._cache: OrderedDict[int, tuple[str, SnapshotData]] = OrderedDict()

    def load(self, season: int) -> SnapshotData:
        token = self._version_for(season)
        if token is None:
            # Delegate so the caller sees a proper SnapshotMissingError.
            return self._inner.load(season)
        cached = self._cache.get(season)
        if cached is not None and cached[0] == token:
            self._cache.move_to_end(season)
            return cached[1]
        data = self._inner.load(season)
        # Atomic dict assignment under GIL — concurrent loads may race
        # and double-parse, but the result is correct.
        self._cache[season] = (token, data)
        self._cache.move_to_end(season)
        while len(self._cache) > self._max_seasons:
            self._cache.popitem(last=False)
        return data


class SnapshotMissingError(RuntimeError):
    """No snapshot folder for the requested season."""


class SnapshotSchemaError(RuntimeError):
    """Snapshot present but unreadable (schema mismatch, malformed)."""


def assemble_snapshot(
    season: int,
    *,
    load_json: Callable[[str], dict[str, object]],
    has_object: Callable[[str], bool],
    location_label: str,
    supported_schema_version: int,
) -> SnapshotData:
    """Parse a season's snapshot from a backend-agnostic byte source.

    ``load_json(name)`` returns the parsed JSON object for ``name``
    (e.g. ``manifest.json``). It must raise ``SnapshotSchemaError`` on
    malformed JSON. The manifest **must exist** when this is called —
    callers should check up-front and raise ``SnapshotMissingError`` if
    not. ``has_object(name)`` is consulted for optional artifacts (only
    the prior-season stats today).
    """

    manifest = load_json(MANIFEST_NAME)
    schema_version = _require_int(manifest, "schema_version", location_label)
    if schema_version > supported_schema_version:
        raise SnapshotSchemaError(
            f"snapshot at {location_label} has schema_version={schema_version}, "
            f"but decision-engine only supports up to "
            f"{supported_schema_version}. Upgrade decision-engine."
        )

    manifest_season = _require_int(manifest, "season", location_label)
    if manifest_season != season:
        raise SnapshotSchemaError(
            f"{location_label}: requested season {season} but manifest "
            f"declares season {manifest_season}"
        )

    weeks_raw = manifest.get("weeks_included") or []
    if not isinstance(weeks_raw, list):
        raise SnapshotSchemaError(
            f"{location_label}/manifest.json: weeks_included must be a list"
        )
    weeks = tuple(int(w) for w in weeks_raw)
    upcoming = manifest.get("upcoming_week_projection")
    upcoming_week = int(upcoming) if isinstance(upcoming, int) else None
    prior_bootstrapped = bool(manifest.get("prior_season_bootstrapped"))

    players_raw = load_json(PLAYERS_NAME)
    players = _players_from_raw(players_raw)

    weekly_stats: dict[int, dict[str, dict[str, float]]] = {}
    for week in weeks:
        name = f"stats_week_{week}.json"
        if not has_object(name):
            raise SnapshotSchemaError(
                f"{location_label}: manifest lists week {week} but {name} is missing"
            )
        weekly_stats[week] = _coerce_stats(load_json(name), label=name)

    prior_season_stats: dict[str, dict[str, float]] = {}
    if prior_bootstrapped:
        if has_object(PRIOR_SEASON_NAME):
            prior_season_stats = _coerce_stats(
                load_json(PRIOR_SEASON_NAME), label=PRIOR_SEASON_NAME
            )
        else:
            log.warning(
                "%s: manifest says prior season bootstrapped but %s missing",
                location_label,
                PRIOR_SEASON_NAME,
            )

    return SnapshotData(
        snapshot_dir=location_label,
        schema_version=schema_version,
        season=manifest_season,
        weeks_included=weeks,
        upcoming_week_projection=upcoming_week,
        players=players,
        weekly_stats=weekly_stats,
        prior_season_stats=prior_season_stats,
    )


class FilesystemSnapshotReader:
    """Reads ``<root>/<season>/`` into a ``SnapshotData``."""

    def __init__(self, root: Path, *, supported_schema_version: int) -> None:
        self._root = root
        self._supported_schema_version = supported_schema_version

    def load(self, season: int) -> SnapshotData:
        snapshot_dir = self._root / str(season)
        if not snapshot_dir.is_dir():
            raise SnapshotMissingError(
                f"no snapshot for season {season} at {snapshot_dir}; "
                "run `stats-loader update` (or use the interactive app) first."
            )
        log.info("Reading snapshot %s", snapshot_dir)

        def load_json(name: str) -> dict[str, object]:
            path = snapshot_dir / name
            if not path.exists():
                raise SnapshotSchemaError(
                    f"{snapshot_dir}: missing {name} (incomplete snapshot)"
                )
            return _load_json_object(path)

        def has_object(name: str) -> bool:
            return (snapshot_dir / name).exists()

        return assemble_snapshot(
            season,
            load_json=load_json,
            has_object=has_object,
            location_label=str(snapshot_dir),
            supported_schema_version=self._supported_schema_version,
        )

    def version_for(self, season: int) -> str | None:
        """Manifest mtime as the cache-invalidation token. ``None`` if missing."""

        try:
            mtime = (self._root / str(season) / MANIFEST_NAME).stat().st_mtime
        except FileNotFoundError:
            return None
        return str(mtime)


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SnapshotSchemaError(f"{path}: malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SnapshotSchemaError(
            f"{path}: expected object at top level, got {type(payload).__name__}"
        )
    return payload


def _players_from_raw(raw: dict[str, object]) -> dict[str, Player]:
    out: dict[str, Player] = {}
    for pid, entry in raw.items():
        if not isinstance(entry, dict):
            log.warning("player %s not an object; skipping", pid)
            continue
        try:
            out[pid] = _player_from_entry(pid, entry)
        except (TypeError, ValueError) as exc:
            log.warning("player %s malformed (%s); skipping", pid, exc)
    return out


def _player_from_entry(pid: str, entry: dict[str, object]) -> Player:
    raw_positions = entry.get("fantasy_positions") or ()
    if not isinstance(raw_positions, list | tuple):
        raw_positions = ()
    return Player(
        player_id=pid,
        full_name=_as_opt_str(entry.get("full_name")),
        position=_as_opt_str(entry.get("position")),
        fantasy_positions=tuple(str(p) for p in raw_positions if isinstance(p, str)),
        team=_as_opt_str(entry.get("team")),
        status=_as_opt_str(entry.get("status")),
        injury_status=_as_opt_str(entry.get("injury_status")),
    )


def _coerce_stats(raw: dict[str, object], *, label: str) -> dict[str, dict[str, float]]:
    """Best-effort coerce ``{pid: {stat: number}}``. Quarantine malformed records."""

    out: dict[str, dict[str, float]] = {}
    for pid, entry in raw.items():
        if not isinstance(entry, dict):
            log.warning("%s: player %s value not an object; skipping", label, pid)
            continue
        coerced: dict[str, float] = {}
        for code, val in entry.items():
            if not isinstance(code, str):
                continue
            if isinstance(val, bool) or not isinstance(val, int | float):
                continue
            coerced[code] = float(val)
        out[pid] = coerced
    return out


def _require_int(manifest: dict[str, object], key: str, location_label: str) -> int:
    value = manifest.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotSchemaError(
            f"{location_label}/manifest.json: {key!r} must be an int, got {value!r}"
        )
    return value


def _as_opt_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
