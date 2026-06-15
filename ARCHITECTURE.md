# Architecture

Fantasy Football Decision Maker's job is to take Sleeper league
data and NFL player stats, snapshot them locally, then score
players for a user's roster decisions against a chosen risk
profile. The pipeline is **staged**: each stage owns one
transformation and hands off through versioned local storage
(snapshot folders), never through direct calls.

---

## Stages

```
sources               stats-loader            local store              decision-engine
┌─────────────┐      ┌────────────┐         ┌──────────────┐         ┌────────────┐
│ Sleeper API │  →   │  Fetch +   │  →      │  data/       │   →     │  Score &   │
│ /v1/...     │      │  snapshot  │         │  snapshots/  │         │  rank      │
└─────────────┘      └────────────┘         │  <date>/     │         └────────────┘
                                            └──────────────┘                │
                                                                            ▼
                                                                        CLI output
```

Only Sleeper is in scope as a data source for v1. `nfl_data_py` and
other sources are deliberately deferred; the snapshot format is
source-pluggable but we are not paying that complexity tax until we
need it.

---

## Storage layer (the contract between stages)

The local snapshot folder is the public interface between
`stats-loader` and `decision-engine`. A stage may be reimplemented
entirely as long as its outputs in this layer remain
backward-compatible.

| Path | Owner | Contract |
| -- | -- | -- |
| `data/snapshots/<YYYY-MM-DD>[-N]/manifest.json` | stats-loader | Commit marker. Records season, weeks included, source URLs, loader version, snapshot timestamps. |
| `data/snapshots/<YYYY-MM-DD>[-N]/players.json` | stats-loader | The full Sleeper `/v1/players/nfl` payload, verbatim. |
| `data/snapshots/<YYYY-MM-DD>[-N]/stats_week_<W>.json` | stats-loader | Per-week NFL stats. One file per completed week of the current season. |
| `data/snapshots/<YYYY-MM-DD>[-N]/projections_week_<W>.json` | stats-loader | Sleeper's own projection for that week. Stored as a baseline. |
| `data/snapshots/<YYYY-MM-DD>[-N]/stats_prior_season.json` | stats-loader | Prior season totals, present only when bootstrapping (early in a new season). |
| `data/snapshots/<YYYY-MM-DD>[-N]/` (the folder) | stats-loader | **Immutable** once renamed into place. Folder name = wallclock date the script finished. Re-runs on the same day append `-2`, `-3`, etc. |

`decision-engine` reads the lexicographically-latest folder in
`data/snapshots/` at every invocation. League-specific data
(rosters, settings, matchups) is **not** snapshotted — it is
fetched live from Sleeper at decide-time, because it changes
per-user and is small.

---

## Layering inside a service

Both services use the same internal layering, enforced by
`import-linter`:

```
types → config → providers → clients → core → entrypoint
```

- `types/` — pure data structures (pydantic models).
- `config/` — env + CLI argument resolution.
- `providers/` — source-specific shapes (e.g. `SleeperPlayer` vs our normalised `Player`).
- `clients/` — concrete I/O (`httpx` for Sleeper, filesystem for snapshots).
- `core/` — pure logic. Accepts clients by parameter; never constructs them.
- `entrypoint/` — CLI wiring (typer).

`core/` is the part the buddies will iterate on (especially the
scoring model in `decision-engine`). It's pure functions over typed
data — easy to unit test, easy to swap.
