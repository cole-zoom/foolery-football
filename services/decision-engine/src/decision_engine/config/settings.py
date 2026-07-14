"""Resolve runtime settings from CLI flags + environment.

The entrypoint builds one ``Settings`` and passes it down. CLI flags win
over env, env wins over defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from decision_engine.types import Pool

DEFAULT_SLEEPER_BASE_URL = "https://api.sleeper.app"
DEFAULT_RISK = 0.5
DEFAULT_LIMIT = 10
DEFAULT_POOL: Pool = "roster"
# blend cleared the PRD 3.4 ship gate on the frozen 100-league sample
# (July 2026): 79/99 leagues beaten, +75.9 avg margin vs human.
DEFAULT_MODEL = "blend"

# We only understand snapshot schemas up to this version. If the loader
# bumps the snapshot format, this bumps with it.
SUPPORTED_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for one ``decide`` invocation."""

    user: str
    league_id: str
    slot: str
    risk: float
    pool: Pool
    limit: int
    model: str
    prefer_team: str | None
    avoid_team: str | None
    season_override: int | None
    week_override: int | None
    snapshot_root: Path
    sleeper_base_url: str


def resolve_settings(
    *,
    user: str,
    league_id: str,
    slot: str,
    risk: float,
    pool: Pool,
    limit: int,
    model: str,
    prefer_team: str | None,
    avoid_team: str | None,
    season: int | None,
    week: int | None,
    snapshot_root: Path | None,
    sleeper_base_url: str | None,
) -> Settings:
    """Build a ``Settings`` from CLI inputs. Validate value ranges.

    ``ValueError`` here surfaces as exit code 1 (user input error) at
    the CLI layer.
    """

    if not user.strip():
        raise ValueError("--user must not be empty")
    if not league_id.strip():
        raise ValueError("--league must not be empty")
    if not slot.strip():
        raise ValueError("--slot must not be empty")
    if not (0.0 <= risk <= 1.0):
        raise ValueError(f"--risk must be in [0.0, 1.0], got {risk}")
    if limit <= 0:
        raise ValueError(f"--limit must be positive, got {limit}")

    resolved_root = (
        snapshot_root
        or _env_path("DECISION_ENGINE_SNAPSHOT_ROOT")
        or _default_snapshot_root()
    )
    resolved_base = (
        sleeper_base_url
        or os.environ.get("DECISION_ENGINE_SLEEPER_BASE_URL")
        or DEFAULT_SLEEPER_BASE_URL
    )

    return Settings(
        user=user.strip(),
        league_id=league_id.strip(),
        slot=slot.strip().upper(),
        risk=risk,
        pool=pool,
        limit=limit,
        model=model.strip(),
        prefer_team=prefer_team.strip().upper() if prefer_team else None,
        avoid_team=avoid_team.strip().upper() if avoid_team else None,
        season_override=season,
        week_override=week,
        snapshot_root=resolved_root,
        sleeper_base_url=resolved_base.rstrip("/"),
    )


def _env_path(key: str) -> Path | None:
    raw = os.environ.get(key)
    return Path(raw).expanduser() if raw else None


def _default_snapshot_root() -> Path:
    """``<repo>/data/seasons`` discovered by walking up from this file.

    Layout: ``<repo>/services/decision-engine/src/decision_engine/config/settings.py``
    """

    here = Path(__file__).resolve()
    repo_root = here.parents[5]
    return repo_root / "data" / "seasons"
