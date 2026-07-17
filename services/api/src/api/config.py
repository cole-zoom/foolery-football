"""Runtime config for the API. Env-driven so deploy targets (Cloud Run,
local) only differ in env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_SLEEPER_BASE_URL = "https://api.sleeper.app"

# The one scoring model production serves. The engine registry keeps
# the other models for the eval harness (evals/) and the CLI; the API
# deliberately exposes no way to select them.
PROD_MODEL = "blend"

SnapshotBackend = Literal["fs", "gcs"]


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
    snapshot_backend: SnapshotBackend
    snapshot_root: Path
    gcs_bucket: str | None
    gcs_prefix: str
    cors_origins: list[str]
    headshot_base_url: str


def load_settings() -> Settings:
    backend_raw = (os.environ.get("FFDM_SNAPSHOT_BACKEND") or "fs").lower()
    if backend_raw not in ("fs", "gcs"):
        raise ValueError(
            f"FFDM_SNAPSHOT_BACKEND must be 'fs' or 'gcs', got {backend_raw!r}"
        )
    backend: SnapshotBackend = backend_raw  # type: ignore[assignment]

    bucket = os.environ.get("FFDM_GCS_BUCKET") or None
    if backend == "gcs" and not bucket:
        raise ValueError("FFDM_SNAPSHOT_BACKEND=gcs requires FFDM_GCS_BUCKET")

    return Settings(
        sleeper_base_url=os.environ.get(
            "FFDM_SLEEPER_BASE_URL", DEFAULT_SLEEPER_BASE_URL
        ).rstrip("/"),
        snapshot_backend=backend,
        snapshot_root=Path(
            os.environ.get("FFDM_SNAPSHOT_ROOT") or _default_snapshot_root()
        ).expanduser(),
        gcs_bucket=bucket,
        gcs_prefix=os.environ.get("FFDM_GCS_PREFIX", "seasons").strip("/"),
        cors_origins=_split_csv(os.environ.get("FFDM_CORS_ORIGINS"))
        or ["http://localhost:5173", "http://127.0.0.1:5173"],
        headshot_base_url=os.environ.get(
            "FFDM_HEADSHOT_BASE_URL", "https://sleepercdn.com/content/nfl/players"
        ).rstrip("/"),
    )
