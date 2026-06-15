# services/stats-loader

The weekly batch job: pulls Sleeper's NFL player metadata, per-week
stats, and per-week projections, and writes them as an immutable
snapshot folder under `data/snapshots/<YYYY-MM-DD>/`.

A "snapshot" is the unit of handoff to the decision engine. The
engine always reads the lexicographically latest snapshot folder.

## Snapshot contents

```
data/snapshots/2026-09-15/
├── manifest.json               # commit marker — season, weeks included, sources, versions, timestamps
├── players.json                # /v1/players/nfl (~5MB, the big one)
├── stats_week_1.json           # /v1/stats/nfl/regular/2026/1
├── stats_week_2.json           # …and so on for every completed week
├── projections_week_1.json     # /v1/projections/nfl/regular/2026/1
└── projections_week_2.json     # …including the next upcoming week
```

When the new season is in its first week (no in-season data yet),
`stats_prior_season.json` is also written so the decision engine
can bootstrap variance estimates from last year.

## Layering

Imports flow forward only, enforced by `import-linter` (config in
`services/stats-loader/pyproject.toml`):

```
types → config → providers → clients → core → entrypoint
```

`core/` is pure logic. Concrete clients (`httpx`, filesystem) live
in `clients/` and are passed into `core.pipeline.run(...)` by
callers — `core` never constructs them.

## Running locally

```bash
cd services/stats-loader
uv sync --extra dev
uv run pytest
uv run ruff check
uv run lint-imports

# Refresh the local snapshot.
uv run stats-loader update

# Dry-run (fetch, validate, but don't write anything to disk).
uv run stats-loader update --dry-run
```

`uv.lock` is committed; don't regenerate casually.

## Scope of this service

Today: Sleeper API → JSON snapshot on disk. **No scheduler, no
cloud, no transformation.** Files are written close to as-received,
with only enough structure to make the snapshot self-describing
(`manifest.json`).

Out of scope (deferred until we know we need them):

- Multi-source ingestion (`nfl_data_py`, etc.).
- Stat normalisation or transformation. The decision engine does
  its own math from the raw snapshot.
- Automated scheduling. The user runs the script manually each
  week.
- Pruning old snapshots. Manual for now; will add a `prune` command
  if disk usage gets annoying.
