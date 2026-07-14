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
- ``<root>/<season>/projections_week_<W>.json`` — Sleeper's pre-kickoff
  stat-level projections, one per week the loader has seen (weeks in
  ``weeks_included`` plus ``upcoming_week_projection``). Optional per
  week — old snapshots without them load with empty
  ``weekly_projections``.

The ``FilesystemSnapshotReader`` lives here; the GCS-backed equivalent
lives in the api package so this package stays free of cloud SDKs.
Both delegate to ``assemble_snapshot`` for parsing.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Final, Protocol

from decision_engine.types import Player, SnapshotData

log = logging.getLogger(__name__)

MANIFEST_NAME: Final[str] = "manifest.json"
PLAYERS_NAME: Final[str] = "players.json"
PRIOR_SEASON_NAME: Final[str] = "stats_prior_season.json"
SCHEDULE_NAME: Final[str] = "schedule.json"
INJURIES_NAME: Final[str] = "injuries.json"


class SnapshotReader(Protocol):
    """Protocol consumed by ``core.pipeline``."""

    def load(self, season: int) -> SnapshotData: ...


class CachingSnapshotReader:
    """In-memory LRU cache around any ``SnapshotReader``.

    Snapshot files are large (~5MB players.json + per-week stats, ~100-200MB
    per season as parsed Python objects). Re-parsing them per request — let
    alone per slot, per prefetched week — is a measurable bottleneck. The
    cache is capped at ``max_seasons`` so we don't OOM after a user pages
    through history, and loads are **single-flight per season** so a burst
    fan-out (e.g. 18 parallel ``/decisions?week=1..18`` calls for one
    season) doesn't allocate N copies of the snapshot in parallel before
    any of them populate the cache. Different seasons still load
    concurrently.

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
        self._registry_lock = threading.Lock()
        self._season_locks: dict[int, threading.Lock] = {}

    def load(self, season: int) -> SnapshotData:
        token = self._version_for(season)
        if token is None:
            # Delegate so the caller sees a proper SnapshotMissingError.
            return self._inner.load(season)

        # Fast path: already cached and fresh. dict.get is atomic under
        # the GIL; we only need the lock to mutate (move_to_end).
        cached = self._cache.get(season)
        if cached is not None and cached[0] == token:
            with self._registry_lock:
                if season in self._cache:
                    self._cache.move_to_end(season)
            return cached[1]

        # Slow path: serialise concurrent loads of the same season so a
        # burst of N requests doesn't allocate N snapshots in flight.
        lock = self._get_season_lock(season)
        with lock:
            # Re-check inside the lock; another thread may have populated
            # the cache while we were waiting.
            cached = self._cache.get(season)
            if cached is not None and cached[0] == token:
                with self._registry_lock:
                    if season in self._cache:
                        self._cache.move_to_end(season)
                return cached[1]

            data = self._inner.load(season)
            with self._registry_lock:
                self._cache[season] = (token, data)
                self._cache.move_to_end(season)
                while len(self._cache) > self._max_seasons:
                    self._cache.popitem(last=False)
            return data

    def _get_season_lock(self, season: int) -> threading.Lock:
        with self._registry_lock:
            lock = self._season_locks.get(season)
            if lock is None:
                lock = threading.Lock()
                self._season_locks[season] = lock
            return lock


class SnapshotMissingError(RuntimeError):
    """No snapshot folder for the requested season."""


class SnapshotSchemaError(RuntimeError):
    """Snapshot present but unreadable (schema mismatch, malformed)."""


def assemble_snapshot(
    season: int,
    *,
    load_json: Callable[[str], object],
    has_object: Callable[[str], bool],
    location_label: str,
    supported_schema_version: int,
) -> SnapshotData:
    """Parse a season's snapshot from a backend-agnostic byte source.

    ``load_json(name)`` returns the parsed JSON value for ``name``
    (e.g. ``manifest.json``) — any top-level type; per-artifact shape
    checks happen here. It must raise ``SnapshotSchemaError`` on
    malformed JSON. The manifest **must exist** when this is called —
    callers should check up-front and raise ``SnapshotMissingError`` if
    not. ``has_object(name)`` is consulted for optional artifacts (the
    prior-season stats and the schedule today).
    """

    manifest = _as_object(load_json(MANIFEST_NAME), MANIFEST_NAME, location_label)
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

    players_raw = _as_object(load_json(PLAYERS_NAME), PLAYERS_NAME, location_label)
    players = _players_from_raw(players_raw)

    weekly_stats: dict[int, dict[str, dict[str, float]]] = {}
    for week in weeks:
        name = f"stats_week_{week}.json"
        if not has_object(name):
            raise SnapshotSchemaError(
                f"{location_label}: manifest lists week {week} but {name} is missing"
            )
        weekly_stats[week] = _coerce_stats(
            _as_object(load_json(name), name, location_label), label=name
        )

    weekly_projections: dict[int, dict[str, dict[str, float]]] = {}
    projection_weeks = set(weeks)
    if upcoming_week is not None:
        projection_weeks.add(upcoming_week)
    for week in sorted(projection_weeks):
        name = f"projections_week_{week}.json"
        if not has_object(name):
            continue
        weekly_projections[week] = _coerce_stats(
            _as_object(load_json(name), name, location_label), label=name
        )

    prior_season_stats: dict[str, dict[str, float]] = {}
    if prior_bootstrapped:
        if has_object(PRIOR_SEASON_NAME):
            prior_season_stats = _coerce_stats(
                _as_object(load_json(PRIOR_SEASON_NAME), PRIOR_SEASON_NAME, location_label),
                label=PRIOR_SEASON_NAME,
            )
        else:
            log.warning(
                "%s: manifest says prior season bootstrapped but %s missing",
                location_label,
                PRIOR_SEASON_NAME,
            )

    schedule: dict[int, dict[str, str]] = {}
    home_teams: dict[int, frozenset[str]] = {}
    if has_object(SCHEDULE_NAME):
        schedule, home_teams = _schedule_from_raw(
            load_json(SCHEDULE_NAME), label=SCHEDULE_NAME
        )

    weekly_injuries: dict[int, dict[str, str]] = {}
    if has_object(INJURIES_NAME):
        weekly_injuries = _injuries_from_raw(
            _as_object(load_json(INJURIES_NAME), INJURIES_NAME, location_label),
            label=INJURIES_NAME,
        )

    version = manifest.get("snapshot_finished_at")

    return SnapshotData(
        snapshot_dir=location_label,
        schema_version=schema_version,
        season=manifest_season,
        weeks_included=weeks,
        upcoming_week_projection=upcoming_week,
        players=players,
        weekly_stats=weekly_stats,
        weekly_projections=weekly_projections,
        prior_season_stats=prior_season_stats,
        schedule=schedule,
        home_teams=home_teams,
        weekly_injuries=weekly_injuries,
        snapshot_version=version if isinstance(version, str) else None,
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

        def load_json(name: str) -> object:
            path = snapshot_dir / name
            if not path.exists():
                raise SnapshotSchemaError(
                    f"{snapshot_dir}: missing {name} (incomplete snapshot)"
                )
            return _load_json_value(path)

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


def _load_json_value(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise SnapshotSchemaError(f"{path}: malformed JSON: {exc}") from exc


def _as_object(payload: object, name: str, location_label: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise SnapshotSchemaError(
            f"{location_label}/{name}: expected object at top level, "
            f"got {type(payload).__name__}"
        )
    return payload


def _schedule_from_raw(
    payload: object, *, label: str
) -> tuple[dict[int, dict[str, str]], dict[int, frozenset[str]]]:
    """``schedule.json`` (list of games) -> opponent map + home teams.

    The opponent map records both directions of every game so a single
    dict lookup answers "who does <team> face in week <W>"; the home
    set preserves which side hosts (the opponent map is symmetric and
    loses that). Malformed games are logged and skipped (quarantine
    over drop).
    """

    if not isinstance(payload, list):
        raise SnapshotSchemaError(
            f"{label}: expected array at top level, got {type(payload).__name__}"
        )

    out: dict[int, dict[str, str]] = {}
    home_sets: dict[int, set[str]] = {}
    for i, game in enumerate(payload):
        if not isinstance(game, dict):
            log.warning("%s: game %d not an object; skipping", label, i)
            continue
        week = game.get("week")
        home = game.get("home")
        away = game.get("away")
        if not isinstance(week, int) or not isinstance(home, str) or not isinstance(away, str):
            log.warning("%s: game %d missing week/home/away; skipping", label, i)
            continue
        week_map = out.setdefault(week, {})
        week_map[home] = away
        week_map[away] = home
        home_sets.setdefault(week, set()).add(home)
    return out, {w: frozenset(s) for w, s in home_sets.items()}


def _injuries_from_raw(
    raw: dict[str, object], *, label: str
) -> dict[int, dict[str, str]]:
    """``injuries.json`` -> week -> player_id -> report_status.

    Shape written by ``scripts/fetch-injuries.py``:
    ``{"<week>": {"<pid>": {"report_status": "Out", ...}}}``. Only the
    game-status string is lifted; malformed weeks/entries are logged
    and skipped (quarantine over drop).
    """

    out: dict[int, dict[str, str]] = {}
    for week_key, table in raw.items():
        try:
            week = int(week_key)
        except (TypeError, ValueError):
            log.warning("%s: week key %r not an int; skipping", label, week_key)
            continue
        if not isinstance(table, dict):
            log.warning("%s: week %s value not an object; skipping", label, week_key)
            continue
        week_out: dict[str, str] = {}
        for pid, entry in table.items():
            if not isinstance(entry, dict):
                log.warning("%s: wk%s player %s not an object; skipping", label, week_key, pid)
                continue
            status = entry.get("report_status")
            if isinstance(status, str) and status:
                week_out[str(pid)] = status
        out[week] = week_out
    return out


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
