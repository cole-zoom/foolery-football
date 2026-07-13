#!/usr/bin/env python3
"""Backfill schedule.json into existing season snapshots.

Snapshots taken before the loader learned to fetch the NFL schedule
(stats_loader >= this change) lack ``schedule.json``. Rather than
re-fetching six full seasons through the loader, this script injects
just the schedule artifact into each existing ``data/seasons/<year>/``
folder and records the source in its manifest.

This deliberately mutates committed snapshots — the one-time exception
to the "manifest is the commit marker" rule. The change is additive:
readers that predate schedule support ignore the extra file and the
extra ``sources`` key. Touching manifest.json also bumps its mtime /
GCS generation, which is what the API's snapshot cache keys on, so the
deployed service picks the new artifact up automatically after
``gsutil -m rsync -r data/seasons gs://<bucket>/seasons``.

Usage (httpx comes from the stats-loader env):

    uv run --project services/stats-loader python scripts/backfill-schedule.py [data/seasons]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE_URL = "https://api.sleeper.app"


def fetch_schedule(season: int) -> list[object]:
    url = f"{BASE_URL}/schedule/nfl/regular/{season}"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or not payload:
        raise SystemExit(f"{url}: expected non-empty array, got {type(payload).__name__}")
    usable = sum(
        1
        for g in payload
        if isinstance(g, dict)
        and isinstance(g.get("week"), int)
        and isinstance(g.get("home"), str)
        and isinstance(g.get("away"), str)
    )
    if usable == 0:
        raise SystemExit(f"{url}: no game has week/home/away — schema change?")
    return payload


def write_json(path: Path, payload: object) -> None:
    # Match the loader's snapshot_writer: sorted keys, readable unicode.
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False)


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/seasons")
    season_dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "manifest.json").exists()
    )
    if not season_dirs:
        raise SystemExit(f"no season snapshots under {root}")

    for season_dir in season_dirs:
        season = int(season_dir.name)
        schedule_path = season_dir / "schedule.json"
        if schedule_path.exists():
            print(f"{season}: schedule.json already present, skipping")
            continue

        payload = fetch_schedule(season)
        write_json(schedule_path, payload)

        manifest_path = season_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("sources", {})["schedule"] = (
            f"{BASE_URL}/schedule/nfl/regular/{season}"
        )
        write_json(manifest_path, manifest)
        print(f"{season}: wrote schedule.json ({len(payload)} games), manifest updated")


if __name__ == "__main__":
    main()
