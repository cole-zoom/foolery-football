"""``ffdm-api`` script entrypoint. Boots uvicorn with reload in dev."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _sibling_service_src_dirs() -> list[str]:
    """Watch source dirs of sibling services so cross-package edits hot reload.

    services/api/src/api/entrypoint.py -> services/ is 3 parents up.
    """

    here = Path(__file__).resolve()
    services_root = here.parents[3]
    out: list[str] = []
    for service in ("api", "app", "decision-engine", "stats-loader"):
        src = services_root / service / "src"
        if src.is_dir():
            out.append(str(src))
    return out


def run() -> None:
    host = os.environ.get("FFDM_API_HOST", "127.0.0.1")
    port = int(os.environ.get("FFDM_API_PORT", "8000"))
    reload = os.environ.get("FFDM_API_RELOAD", "1") == "1"
    kwargs: dict[str, object] = {
        "host": host,
        "port": port,
        "reload": reload,
        "log_level": os.environ.get("FFDM_API_LOG_LEVEL", "info"),
    }
    if reload:
        kwargs["reload_dirs"] = _sibling_service_src_dirs()
    uvicorn.run("api.main:app", **kwargs)


if __name__ == "__main__":
    run()
