#!/usr/bin/env python3
"""Fetch official NFL injury reports (nflverse) into season snapshots.

Downloads the nflverse ``injuries_<season>.csv`` releases (the league's
weekly Wed-Fri injury reports — published pre-kickoff, so using week
W's report to gate week-W lineups is leakage-safe) plus the
dynastyprocess player-ID crosswalk, joins gsis_id -> sleeper_id, and
writes ``data/seasons/<season>/injuries.json``:

    {"<week>": {"<sleeper_id>": {"report_status": "Out",
                                 "practice_status": "..."}}}

Only rows with a non-empty ``report_status`` (Out / Doubtful /
Questionable) are kept — that's the game-status designation the
availability gate reads. Rows whose player has no sleeper_id mapping
are logged and skipped (quarantine over drop).

Sources (both free, no keys — recorded in
docs/references/external-data.md):
- https://github.com/nflverse/nflverse-data/releases (injuries)
- https://github.com/dynastyprocess/data (db_playerids.csv)

Usage:
    uv run --project services/decision-engine python scripts/fetch-injuries.py \
        [--seasons 2021-2025] [--root data/seasons]

Re-running overwrites injuries.json in place — it's a derived artifact
of upstream data, not part of the loader's immutable manifest set. If
the season also lives in GCS, re-sync and re-upload manifest.json (the
generation bump is what invalidates the API's snapshot cache).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path

import httpx

INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "injuries/injuries_{season}.csv"
)
CROSSWALK_URL = (
    "https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv"
)

REPORT_STATUSES = {"Out", "Doubtful", "Questionable"}


def fetch_csv(url: str) -> list[dict[str, str]]:
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def build_crosswalk() -> dict[str, str]:
    rows = fetch_csv(CROSSWALK_URL)
    out: dict[str, str] = {}
    for r in rows:
        gsis = (r.get("gsis_id") or "").strip()
        sleeper = (r.get("sleeper_id") or "").strip()
        if gsis and sleeper:
            out[gsis] = sleeper
    print(f"crosswalk: {len(out)} gsis_id -> sleeper_id mappings")
    return out


def season_injuries(
    season: int, crosswalk: dict[str, str]
) -> tuple[dict[str, dict[str, dict[str, str]]], int, int]:
    rows = fetch_csv(INJURIES_URL.format(season=season))
    weeks: dict[str, dict[str, dict[str, str]]] = {}
    kept = missed = 0
    for r in rows:
        if r.get("season_type") not in (None, "", "REG"):
            continue
        status = (r.get("report_status") or "").strip()
        if status not in REPORT_STATUSES:
            continue
        gsis = (r.get("gsis_id") or "").strip()
        sleeper = crosswalk.get(gsis)
        if sleeper is None:
            missed += 1
            print(f"  no sleeper_id for {gsis} ({r.get('full_name')}); skipping")
            continue
        week = str(int(r["week"]))
        weeks.setdefault(week, {})[sleeper] = {
            "report_status": status,
            "practice_status": (r.get("practice_status") or "").strip(),
        }
        kept += 1
    return weeks, kept, missed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seasons", default="2021-2025", help="inclusive range, e.g. 2021-2025")
    ap.add_argument("--root", type=Path, default=Path("data/seasons"))
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.seasons.split("-"))
    crosswalk = build_crosswalk()

    for season in range(lo, hi + 1):
        season_dir = args.root / str(season)
        if not season_dir.is_dir():
            print(f"{season}: no snapshot dir at {season_dir}; skipping")
            continue
        weeks, kept, missed = season_injuries(season, crosswalk)
        path = season_dir / "injuries.json"
        path.write_text(json.dumps(weeks, indent=None, sort_keys=True) + "\n")
        outs = sum(
            1
            for by_pid in weeks.values()
            for v in by_pid.values()
            if v["report_status"] == "Out"
        )
        print(
            f"{season}: wrote {path} — {kept} designations "
            f"({outs} Out) across {len(weeks)} weeks, {missed} unmapped"
        )


if __name__ == "__main__":
    main()
