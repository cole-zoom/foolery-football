Last updated: 2026-06-12

# PRD: Local stats store (milestone 1 master)

This is the **why / scope** for milestone 1. The how lives in the
implementation PRDs:

- [1.1 — Player metadata](milestone-1/1.1-player-metadata.md)
- [1.2 — Weekly stats + projections](milestone-1/1.2-weekly-stats.md)
- [1.3 — Local storage layout](milestone-1/1.3-local-storage-layout.md)

If you're implementing, read the relevant 1.x PRD directly.

---

## 1. Context

The decision engine needs a consistent local view of (a) every NFL
player Sleeper knows about, with metadata for filtering, and (b)
per-week NFL stats and projections for those players. Both come
from the Sleeper API.

The author of this repo does not play fantasy football. The
contributors who do (the "buddies") write the scoring logic
downstream. This milestone's job is to give them a clean,
predictable, easy-to-read local data store so they don't have to
think about HTTP, retries, schemas, or pagination.

## 2. Goals

### 1.1 — Player metadata

- Snapshot Sleeper's `/v1/players/nfl` to disk.
- Treated as relatively static (Sleeper requests ≤1 fetch/day).
- One file per snapshot, raw shape from Sleeper.

### 1.2 — Weekly stats + projections

- For each completed week of the current NFL season, fetch
  `/v1/stats/nfl/regular/<season>/<week>` and
  `/v1/projections/nfl/regular/<season>/<week>`.
- Also fetch the upcoming week's projections (used as a baseline
  comparison in the decision engine).
- If the current season has zero completed weeks, also fetch the
  prior season's full stats — used to bootstrap variance estimates.
- Each week is its own file in the snapshot.

### 1.3 — Local storage layout

- A snapshot is a folder under `data/snapshots/<YYYY-MM-DD>/`.
- Every run writes a *new* folder. If today's folder exists, append
  `-2`, `-3`, etc.
- The decision engine consumes only the lexicographically latest.
- A `manifest.json` in each snapshot documents what's inside.
- Atomic write protocol: temp folder → rename → final name. A
  crashed run never leaves a half-written snapshot visible.

## 3. Non-goals

- No transformation. Files are stored close to the Sleeper response
  shape.
- No multi-source ingestion. `nfl_data_py` is explicitly out of
  scope for v1.
- No scheduling. The user runs the script manually.
- No upload to cloud storage.
- No pruning of old snapshots. Manual cleanup for now.

## 4. Success criteria

- After running `stats-loader update` on a Tuesday during the NFL
  season, the latest snapshot contains player metadata + stats for
  weeks 1..N where N is the most-recently-completed week.
- The snapshot can be opened by hand with `jq` and human-read.
- A subsequent run on the same day produces a sibling folder
  (`<date>-2`), not an overwrite.
- A crashed mid-write run leaves no visible snapshot — only a
  `.tmp-...` folder that gets cleaned up on the next run.
