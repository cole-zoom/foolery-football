"""Read a per-season snapshot folder into memory.

Concrete filesystem I/O. Pure ``core`` accepts the in-memory result via
a protocol — see ``decision_engine.core.pipeline``.

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
"""

from __future__ import annotations

import json
import logging
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
    """In-memory cache around any ``SnapshotReader``.

    Snapshot files are large (~5MB players.json + per-week stats) and
    fully immutable until the stats-loader overwrites them. Re-parsing
    them per request — let alone per slot, per prefetched week — is a
    measurable bottleneck under fan-out load.

    Keyed by season, invalidated by manifest mtime so a snapshot
    refresh on disk is picked up automatically.
    """

    def __init__(self, inner: SnapshotReader, root: Path) -> None:
        self._inner = inner
        self._root = root
        self._cache: dict[int, tuple[float, SnapshotData]] = {}

    def load(self, season: int) -> SnapshotData:
        manifest_path = self._root / str(season) / MANIFEST_NAME
        try:
            mtime = manifest_path.stat().st_mtime
        except FileNotFoundError:
            # Delegate so the caller sees a proper SnapshotMissingError.
            return self._inner.load(season)
        cached = self._cache.get(season)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        data = self._inner.load(season)
        # Atomic dict assignment under GIL — concurrent loads may race
        # and double-parse, but the result is correct.
        self._cache[season] = (mtime, data)
        return data


class SnapshotMissingError(RuntimeError):
    """No snapshot folder for the requested season."""


class SnapshotSchemaError(RuntimeError):
    """Snapshot present but unreadable (schema mismatch, malformed)."""


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

        manifest = self._read_manifest(snapshot_dir)
        schema_version = _require_int(manifest, "schema_version")
        if schema_version > self._supported_schema_version:
            raise SnapshotSchemaError(
                f"snapshot at {snapshot_dir} has schema_version={schema_version}, "
                f"but decision-engine only supports up to "
                f"{self._supported_schema_version}. Upgrade decision-engine."
            )

        manifest_season = _require_int(manifest, "season")
        if manifest_season != season:
            raise SnapshotSchemaError(
                f"{snapshot_dir}: requested season {season} but manifest "
                f"declares season {manifest_season}"
            )

        weeks_raw = manifest.get("weeks_included") or []
        if not isinstance(weeks_raw, list):
            raise SnapshotSchemaError(
                f"{snapshot_dir}/manifest.json: weeks_included must be a list"
            )
        weeks = tuple(int(w) for w in weeks_raw)
        upcoming = manifest.get("upcoming_week_projection")
        upcoming_week = int(upcoming) if isinstance(upcoming, int) else None
        prior_bootstrapped = bool(manifest.get("prior_season_bootstrapped"))

        players = self._read_players(snapshot_dir)
        weekly_stats = self._read_weekly_stats(snapshot_dir, weeks)
        prior_season_stats = (
            self._read_prior_season(snapshot_dir) if prior_bootstrapped else {}
        )

        return SnapshotData(
            snapshot_dir=str(snapshot_dir),
            schema_version=schema_version,
            season=manifest_season,
            weeks_included=weeks,
            upcoming_week_projection=upcoming_week,
            players=players,
            weekly_stats=weekly_stats,
            prior_season_stats=prior_season_stats,
        )

    def _read_manifest(self, snapshot_dir: Path) -> dict[str, object]:
        path = snapshot_dir / MANIFEST_NAME
        if not path.exists():
            raise SnapshotSchemaError(
                f"{snapshot_dir}: missing manifest.json (incomplete snapshot)"
            )
        return _load_json_object(path)

    def _read_players(self, snapshot_dir: Path) -> dict[str, Player]:
        path = snapshot_dir / PLAYERS_NAME
        if not path.exists():
            raise SnapshotSchemaError(f"{snapshot_dir}: missing players.json")
        raw = _load_json_object(path)

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

    def _read_weekly_stats(
        self,
        snapshot_dir: Path,
        weeks: tuple[int, ...],
    ) -> dict[int, dict[str, dict[str, float]]]:
        out: dict[int, dict[str, dict[str, float]]] = {}
        for week in weeks:
            path = snapshot_dir / f"stats_week_{week}.json"
            if not path.exists():
                raise SnapshotSchemaError(
                    f"{snapshot_dir}: manifest lists week {week} "
                    f"but stats_week_{week}.json is missing"
                )
            out[week] = _coerce_stats(_load_json_object(path), label=path.name)
        return out

    def _read_prior_season(self, snapshot_dir: Path) -> dict[str, dict[str, float]]:
        path = snapshot_dir / PRIOR_SEASON_NAME
        if not path.exists():
            log.warning(
                "%s: manifest says prior season bootstrapped but %s missing",
                snapshot_dir,
                PRIOR_SEASON_NAME,
            )
            return {}
        return _coerce_stats(_load_json_object(path), label=path.name)


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


def _require_int(manifest: dict[str, object], key: str) -> int:
    value = manifest.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotSchemaError(f"manifest.json: {key!r} must be an int, got {value!r}")
    return value


def _as_opt_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
