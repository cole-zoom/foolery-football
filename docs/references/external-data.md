# External data sources (non-Sleeper)

Everything the system consumes that doesn't come from the Sleeper API.
Same spirit as [`sleeper-api.md`](sleeper-api.md): every source we
depend on is recorded here, with its fetch path and failure notes.

## nflverse — official NFL injury reports

- **What:** the league's weekly Wed–Fri injury reports (game status
  Out / Doubtful / Questionable + practice participation), archived
  per season back to 2009. Published **pre-kickoff**, so week-W
  reports are leakage-safe inputs for week-W lineup decisions — same
  contract as Sleeper's weekly projections (PRD 3.1).
- **URL:** `https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_<season>.csv`
- **Auth:** none. Community-maintained (nflverse/nflfastR project),
  CC-licensed data releases.
- **Consumed by:** `scripts/fetch-injuries.py` → per-season
  `data/seasons/<season>/injuries.json` → `SnapshotData.weekly_injuries`
  → the `availability="news"` pipeline gate (milestone 4 run C).
- **Keys on** `gsis_id` (NFL GSIS player IDs) — joined to Sleeper IDs
  via the crosswalk below. Unmapped rows in 2021–2025 are exclusively
  offensive linemen / long snappers — zero fantasy-relevant misses.

## dynastyprocess — player-ID crosswalk

- **What:** one row per player mapping ~12 fantasy-ecosystem IDs
  (`gsis_id`, `sleeper_id`, `espn_id`, …).
- **URL:** `https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv`
- **Auth:** none.
- **Consumed by:** `scripts/fetch-injuries.py` only — the join happens
  at fetch time so the engine never sees non-Sleeper IDs.

## Refresh procedure

```bash
uv run --project services/decision-engine python scripts/fetch-injuries.py \
    --seasons 2021-2025
```

Overwrites `injuries.json` in each season dir (a derived artifact, not
part of the loader's manifest set). For GCS-served environments,
re-sync the bucket **and re-upload `manifest.json`** — its generation
bump is what invalidates the API's in-process snapshot cache.
