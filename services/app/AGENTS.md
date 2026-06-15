# services/app — agent-facing rules

Interactive CLI that orchestrates `stats-loader` (download) and
`decision-engine` (score). Designed to be swapped for a web frontend
without touching `session.py`.

## Read first

- Root [`AGENTS.md`](../../AGENTS.md) — core repo rules.
- [`README.md`](README.md) — what it does and how to run it.
- [`../decision-engine/AGENTS.md`](../decision-engine/AGENTS.md) and
  [`../stats-loader/AGENTS.md`](../stats-loader/AGENTS.md) for the
  wrapped services.

## Module rules

1. **`session.py` is the public API.** A future web UI imports from it.
   It must not prompt, render, or write to stdout. Inputs are
   structured (`AppRequest`); outputs are structured (`DecideResult`).
2. **`cli.py` is disposable.** All prompting, defaulting, looping, and
   table rendering live here. Replace it freely.
3. **Don't reach into the wrapped services' private modules.** Only
   import public symbols (`stats_loader.core.pipeline`,
   `decision_engine.core.pipeline`, the public client classes, the
   declared exceptions).
4. **Cache rules are load-bearing.** Past seasons are immutable; the
   current season refreshes when stale. If a buddy tweaks this, update
   the tests in `tests/unit/test_season_cache.py`.

## Layering

`cli -> session -> season_cache -> types`. Enforced by `import-linter`.
