# services/stats-loader

Weekly batch that snapshots Sleeper player metadata + NFL stats to
`data/snapshots/<YYYY-MM-DD>/`. The decision engine reads the
lexicographically latest snapshot.

For the full spec see
[`docs/product-specs/milestone-1-stats-store.md`](../../docs/product-specs/milestone-1-stats-store.md).
For agent-facing rules see [`AGENTS.md`](AGENTS.md).

## Setup

```bash
uv sync --extra dev
```

`uv.lock` is committed; don't regenerate casually.

## Commands

```bash
# Fetch from Sleeper and write a snapshot under ../../data/snapshots/<today>/.
uv run stats-loader update

# Fetch + validate, but don't touch the filesystem.
uv run stats-loader update --dry-run

# Replay a specific past week (useful for fixtures / debugging).
uv run stats-loader update --season 2025 --week 5

# Write to an alternate snapshot root.
uv run stats-loader update --snapshot-root /tmp/snapshots
```

## Quality gates

```bash
uv run ruff check
uv run mypy
uv run lint-imports
uv run pytest
```

Integration test is opt-in (it hits the real Sleeper API):

```bash
STATS_LOADER_INTEGRATION=1 uv run pytest -m integration
```

## Layout

```
src/stats_loader/
  types.py              # pydantic models
  config/               # CLI/env resolution
  providers/sleeper.py  # response shape validation
  clients/
    http.py             # httpx + retry/backoff
    snapshot_writer.py  # atomic tmp -> rename
  core/
    state.py            # season/week math
    manifest.py         # manifest builder
    pipeline.py         # orchestration
  entrypoint.py         # typer CLI

tests/
  unit/                 # core + clients + providers
  integration/          # live Sleeper smoke test
```

Layering is enforced by `import-linter`. See `AGENTS.md`.
