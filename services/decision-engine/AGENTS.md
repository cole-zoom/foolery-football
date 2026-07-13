# AGENTS.md — services/decision-engine

Service-local overrides apply on top of the repo root [`AGENTS.md`](../../AGENTS.md). Read both before editing here.

## What this service is

CLI that scores Sleeper roster + waiver players for a single slot,
given a risk profile. Reads the latest snapshot from
`data/snapshots/`; fetches league context (rosters, settings) live
from Sleeper on every invocation.

The full spec lives in
[`docs/product-specs/milestone-2-decision-engine.md`](../../docs/product-specs/milestone-2-decision-engine.md)
and three sub-PRDs (2.1, 2.2, 2.3). Architecture (layering) lives in
[`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Layering — non-negotiable

Imports flow forward only. Enforced by `import-linter`. Run
`uv run lint-imports` before opening a PR.

```
types -> config -> providers -> clients -> core -> entrypoint
```

- `types.py` — pydantic models. No I/O. No HTTP.
- `config/` — CLI/env resolution. Pure parsing.
- `providers/sleeper.py` — shape validation of Sleeper responses.
- `clients/http.py` — concrete httpx wrapper with retry/backoff. The only place HTTP happens.
- `clients/snapshot_reader.py` — concrete filesystem reader. Only place we read the snapshot.
- `core/` — pure orchestration. Accepts clients via protocols; never constructs them.
- `core/scoring/` — `naive.py` is the reference impl. Add sibling modules for smarter models and register them in `core/scoring/__init__.py`.
- `entrypoint.py` — typer CLI. Constructs concrete clients and hands them to `core.pipeline.run`.

If you need to break the layering, add a doc note and a real reason,
and update the import-linter contract in `pyproject.toml`. Don't smuggle.

## Scoring is the plug point

`core/scoring/naive.py` is intentionally dumb. Adding a smarter model:

1. New sibling module exposing `def build(snapshot) -> ScoreFn`.
2. Register it in `MODELS` in `core/scoring/__init__.py`.
3. Users select it via `--model <name>`.

No edits to `core/pipeline.py` or `entrypoint.py`. If you find yourself
needing to edit those for a new model, push back on the abstraction.

## Quarantine over drop

Per the repo `AGENTS.md`: malformed individual records get logged and
skipped. Whole-response failures abort the run. No `try/except: pass`.

## Running locally

```bash
uv sync --extra dev
uv run pytest
uv run ruff check
uv run mypy
uv run lint-imports

uv run decide --user <username> --league <league_id> --slot FLEX --risk 0.3
```

## Sleeper specifics

- Endpoints we use live in
  [`docs/references/sleeper-api.md`](../../docs/references/sleeper-api.md).
  Decision engine uses: `/v1/state/nfl`, `/v1/user/<username>`,
  `/v1/user/<id>/leagues/nfl/<season>`, `/v1/league/<id>`,
  `/v1/league/<id>/rosters`, `/v1/league/<id>/matchups/<week>`.
- Retries match stats-loader: 3 attempts on 429 / 5xx, no retry on 4xx.
