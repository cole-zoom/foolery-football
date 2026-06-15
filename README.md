# Fantasy Football Decision Maker

Pulls Sleeper league data and NFL stats into a local snapshot, then
scores roster and waiver players against a user-chosen risk profile
and returns ranked suggestions via CLI.

## Status

Pre-implementation. The repo currently contains only the documentation
scaffold for agent-driven development. No service code exists yet.

## Working on this repo

If you're an agent, **read [`AGENTS.md`](AGENTS.md) before any task.**

If you're a human:

- [`AGENTS.md`](AGENTS.md) — how agents should work in this repo.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — top-level pipeline and layering.
- [`docs/index.md`](docs/index.md) — full doc index.

## Layout

- `services/stats-loader/` — weekly batch that snapshots Sleeper + NFL stats to `data/snapshots/<YYYY-MM-DD>/`.
- `services/decision-engine/` — CLI that reads the latest snapshot and scores players for a given user / league / risk profile.
- `data/snapshots/` — gitignored. Each run writes a new dated folder; the latest wins.
- `docs/` — the context map.

## Running locally

This is local-only. No cloud, no infra, no scheduler — just two
Python services with CLI entrypoints.

```bash
# Refresh the local snapshot (run weekly during NFL season).
cd services/stats-loader
uv sync --extra dev
uv run stats-loader update

# Get a scored ranking for a slot in your league.
cd services/decision-engine
uv sync --extra dev
uv run decide --user <sleeper_username> --league <league_id> \
              --slot FLEX --risk 0.3
```

## License

TBD.
