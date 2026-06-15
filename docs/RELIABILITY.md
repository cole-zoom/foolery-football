Last updated: 2026-06-12

# Reliability bars

Fantasy Football Decision Maker is **not** a realtime, user-facing
service. It's a local CLI run on demand. Bars are set accordingly.

- **Snapshot runs are best-effort.** A failed `stats-loader update`
  is not an incident; rerun it. If the Sleeper API is down or
  partial mid-run, no partial snapshot is committed — the new
  folder is only renamed into place once all files have been
  written successfully and the `manifest.json` is sealed.
- **Snapshots are immutable.** Once a `data/snapshots/<date>/`
  folder exists, nothing rewrites it. Rerunning the loader on the
  same day produces a sibling folder (e.g. `2026-09-15-2`), not an
  overwrite. The decision engine reads the lexicographically latest.
- **No silent failures.** If an individual player or stats record
  is malformed, log structured and skip *that record*. Whole-response
  failures abort the run; never absorb them to "make progress."
  `try/except: pass` is forbidden.
- **Decision engine reads live league data, not snapshots.** League
  settings, rosters, and matchups are fetched fresh on every CLI
  invocation. Stale snapshot stats are acceptable (the user can
  rerun the loader); stale rosters are not.
- **Retries are bounded.** Sleeper API calls retry with exponential
  backoff, max 3 attempts. No infinite retries. No retries on 4xx
  responses except 429.

Specific thresholds live with the code that enforces them.
