# AGENTS.md — services/stats-loader

Service-local overrides apply on top of the repo root [`AGENTS.md`](../../AGENTS.md). Read both before editing here.

## What this service is

Weekly batch job that snapshots Sleeper's NFL player metadata, per-week
stats, and per-week projections to `data/snapshots/<YYYY-MM-DD>/`.
Snapshots are immutable; see PRD 1.3.

The full spec lives in
[`docs/product-specs/milestone-1-stats-store.md`](../../docs/product-specs/milestone-1-stats-store.md)
and the three sub-PRDs (1.1, 1.2, 1.3). Architecture (layering) lives
in [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Layering — non-negotiable

Imports flow forward only. Enforced by `import-linter`. Run
`uv run lint-imports` before opening a PR.

```
types -> config -> providers -> clients -> core -> entrypoint
```

- `types.py` — pydantic models. No I/O. No HTTP.
- `config/` — CLI/env resolution. Pure parsing.
- `providers/sleeper.py` — shape validation of Sleeper responses. Pure functions over decoded JSON.
- `clients/http.py` — concrete httpx wrapper with retry/backoff. The only place HTTP happens.
- `clients/snapshot_writer.py` — concrete filesystem writer. Atomic tmp -> rename. Only place we touch the filesystem (besides tests).
- `core/` — pure orchestration. Accepts clients via protocols; never constructs them.
- `entrypoint.py` — typer CLI. Constructs concrete clients and hands them to `core.pipeline.run`.

If you need to break the layering, add a doc note and a real reason, and update the import-linter contract in `pyproject.toml`. Don't smuggle.

## Snapshots are immutable

Once a snapshot folder is renamed into place, nothing rewrites it.
Re-runs on the same day produce `-2`, `-3`, ... siblings. See PRD 1.3
for the atomic write protocol; the test
`tests/unit/test_snapshot_writer.py` is the contract.

## Quarantine over drop

Per the repo `AGENTS.md`: malformed individual records get logged and
skipped. Whole-response failures abort the run. No `try/except: pass`.

## Running locally

```bash
uv sync --extra dev
uv run pytest                            # unit tests only by default
STATS_LOADER_INTEGRATION=1 uv run pytest # includes the live-Sleeper test
uv run ruff check
uv run mypy
uv run lint-imports

uv run stats-loader update               # write a real snapshot
uv run stats-loader update --dry-run     # fetch + validate, no disk writes
```

## Sleeper specifics

- Endpoints we use are inventoried in
  [`docs/references/sleeper-api.md`](../../docs/references/sleeper-api.md).
  Several are undocumented but working. If you discover or break one,
  update that file in the same PR.
- Retries: bounded exponential backoff, max 3 attempts, on 429 / 5xx.
  4xx (other than 429) is fatal. See `clients/http.py`.
