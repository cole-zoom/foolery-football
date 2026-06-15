"""Runtime config for the API. Env-driven so deploy targets (Cloud Run,
local, eventual GCS-backed snapshots) only differ in env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SLEEPER_BASE_URL = "https://api.sleeper.app"


def _default_snapshot_root() -> Path:
    # services/api/src/api/config.py -> repo_root is 4 parents up.
    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    return repo_root / "data" / "seasons"


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class Settings:
    sleeper_base_url: str
    snapshot_root: Path
    cors_origins: list[str]
    headshot_base_url: str


def load_settings() -> Settings:
    return Settings(
        sleeper_base_url=os.environ.get(
            "FFDM_SLEEPER_BASE_URL", DEFAULT_SLEEPER_BASE_URL
        ).rstrip("/"),
        snapshot_root=Path(
            os.environ.get("FFDM_SNAPSHOT_ROOT") or _default_snapshot_root()
        ).expanduser(),
        cors_origins=_split_csv(os.environ.get("FFDM_CORS_ORIGINS"))
        or ["http://localhost:5173", "http://127.0.0.1:5173"],
        headshot_base_url=os.environ.get(
            "FFDM_HEADSHOT_BASE_URL", "https://sleepercdn.com/content/nfl/players"
        ).rstrip("/"),
    )
