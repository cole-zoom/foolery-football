# services/app

Interactive CLI for the Fantasy Football Decision Maker. Wraps
[`services/stats-loader`](../stats-loader/) (download) and
[`services/decision-engine`](../decision-engine/) (score).

The package is split so a web UI can call `session.decide(...)` later
without touching the terminal layer.

## Setup

```bash
uv sync --extra dev
```

The `stats-loader` and `decision-engine` packages are picked up as path
dependencies (see `pyproject.toml`).

## Run

```bash
uv run ffdm
```

You'll be prompted for:

1. **League ID** — your Sleeper league.
2. **Username (or user_id)** — Sleeper accepts either.
3. **Season** — defaults to the live Sleeper state. Past seasons are
   downloaded once and cached forever.
4. **Slot** — `QB`/`RB`/`WR`/`TE`/`K`/`DEF`/`FLEX`/`WRRB_FLEX`/`WRT_FLEX`/`SUPER_FLEX`.
5. **Risk** — `0.0` safe → `1.0` gamble.
6. **Week** — defaults to the most recent completed week. Replay week N
   scores using stats through N-1.

After the table prints you can keep replaying weeks until you quit.

## Storage

```
data/seasons/2025/
data/seasons/2024/
...
```

Per-year folders. Past seasons are immutable once downloaded. The
current season refreshes when the cache is >24h old or behind the live
NFL state.

## Quality gates

```bash
uv run ruff check
uv run mypy
uv run lint-imports
uv run pytest
```

## Layout

```
src/ffdm_app/
  types.py          # AppRequest, LiveState, SeasonInfo
  season_cache.py   # ensure_season -- per-year download/refresh
  session.py        # decide() -- the API the future web UI calls
  cli.py            # typer prompts + table rendering
```

Layering enforced by `import-linter`: `cli -> session -> season_cache -> types`.
