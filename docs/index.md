# Docs index

## Architecture
- [Top-level architecture](../ARCHITECTURE.md)

## Services
- [`services/stats-loader`](services/stats-loader.md) — weekly Sleeper + NFL stats snapshot job.
- [`services/decision-engine`](services/decision-engine.md) — CLI that scores players for a user's lineup decisions.

## Policy
- [Reliability](RELIABILITY.md)
- [Security](SECURITY.md)

## References
- [Sleeper API endpoint inventory](references/sleeper-api.md) — including endpoints the official docs omit.
- [External data sources](references/external-data.md) — nflverse injury reports + player-ID crosswalk (non-Sleeper inputs).
- [Fantasy football glossary](references/fantasy-glossary.md) — what every domain term means in this codebase.
- [2026 NFL week 1 schedule](references/nfl-2026-week-1.md) — test fixture.

## Product specs
- [Milestone 1 — Local stats store](product-specs/milestone-1-stats-store.md)
  - [1.1 — Player metadata](product-specs/milestone-1/1.1-player-metadata.md)
  - [1.2 — Weekly stats + projections](product-specs/milestone-1/1.2-weekly-stats.md)
  - [1.3 — Local storage layout](product-specs/milestone-1/1.3-local-storage-layout.md)
- [Milestone 2 — Decision engine](product-specs/milestone-2-decision-engine.md)
  - [2.1 — Sleeper league fetch](product-specs/milestone-2/2.1-sleeper-league-fetch.md)
  - [2.2 — Scoring model](product-specs/milestone-2/2.2-scoring-model.md)
  - [2.3 — CLI output](product-specs/milestone-2/2.3-cli-output.md)
- [Milestone 3 — Model upgrade: close the gap to the median human](product-specs/milestone-3-model-upgrade.md)
  - [3.1 — Projections plumbing + availability gate](product-specs/milestone-3/3.1-projections-and-availability-gate.md)
  - [3.2 — Blend model](product-specs/milestone-3/3.2-blend-model.md)
  - [3.3 — Lineup assembly](product-specs/milestone-3/3.3-lineup-assembly.md)
  - [3.4 — Eval attribution + ship gate](product-specs/milestone-3/3.4-eval-attribution-and-ship-gate.md)
- [Milestone 4 — Scratch model: a sleeperless blend](product-specs/milestone-4-scratch-model.md)
