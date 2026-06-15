Last updated: 2026-06-12

# Security bars

## Source surface

- The Sleeper API is **public and read-only**. We never authenticate
  to it. If a future source requires auth, it needs explicit design
  review before adoption.
- We never write back to Sleeper or any source. The pipeline is
  one-way.

## User identity

- The only "user input" we accept is a Sleeper username, league ID,
  position slot, and risk preference. Sleeper usernames are public.
- We do not store or transmit user data anywhere. Everything lives
  on the local filesystem.
- League-specific data (rosters, matchups) is fetched per-invocation
  and held in memory; it is not written to disk.

## Storage

- Snapshots are local files. No remote backend.
- `data/snapshots/` is gitignored. Do not commit snapshots to the
  repo — they're large, regenerable, and contain a frozen view of
  third-party data we don't own.

## Dependencies

- New outbound dependencies are reviewed and noted in the PR
  description.
- Prefer well-known, well-maintained libraries over new unknowns.
- Direct dependencies are pinned; the resolved lockfile (`uv.lock`)
  is committed per service.
- No inline secrets, ever. There are no secrets to manage today —
  if that changes (e.g. an API key for an alt data source), it
  needs a design review before landing.
