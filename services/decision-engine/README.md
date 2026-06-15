# services/decision-engine

CLI that scores Sleeper players for a single roster slot, given a risk
profile. Reads the latest local snapshot written by
[`services/stats-loader`](../stats-loader/); fetches league context
(rosters, settings, matchups) live from Sleeper on each run.

For the full spec see
[`docs/product-specs/milestone-2-decision-engine.md`](../../docs/product-specs/milestone-2-decision-engine.md).
For agent-facing rules see [`AGENTS.md`](AGENTS.md).

## Setup

```bash
uv sync --extra dev
```

You need a snapshot on disk first. From `services/stats-loader/`:

```bash
uv run stats-loader update
```

## Commands

```bash
# Score FLEX candidates on your roster, slightly safe (risk 0.3).
uv run decide --user cole --league 1234567890 --slot FLEX --risk 0.3

# Score waiver-wire WRs with a high-upside risk profile.
uv run decide --user cole --league 1234567890 --slot WR --risk 0.9 --pool waivers

# Replay against a fixed week (skips the live state lookup).
uv run decide --user cole --league 1234567890 --slot QB --season 2024 --week 3

# Boost / penalise specific NFL teams.
uv run decide --user cole --league 1234567890 --slot FLEX \
              --prefer-team DET --avoid-team CHI
```

## Flags

| Flag | Default | Meaning |
| -- | -- | -- |
| `--user` | required | Sleeper username. |
| `--league` | required | Sleeper league ID — must be one of the user's leagues for the current season. |
| `--slot` | required | `QB`/`RB`/`WR`/`TE`/`K`/`DEF`/`FLEX`/`WRRB_FLEX`/`WRT_FLEX`/`SUPER_FLEX`. |
| `--risk` | `0.5` | `0.0` = safest, `1.0` = max gamble. |
| `--prefer-team` | none | NFL team code; +10% multiplier on score. |
| `--avoid-team` | none | NFL team code; −10% multiplier on score. |
| `--pool` | `roster` | `roster` / `waivers` / `both`. |
| `--limit` | `10` | Cap on rows printed. |
| `--season` / `--week` | live | Override `/v1/state/nfl` (must be supplied together). |
| `--model` | `naive` | Scoring model from `core/scoring/__init__.py:MODELS`. |
| `--snapshot-root` | `<repo>/data/snapshots` | Override snapshot folder root. |
| `--sleeper-base-url` | `https://api.sleeper.app` | Override for tests/fixtures. |
| `--log-level` | `WARNING` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |

## Exit codes

- `0` — printed a ranked table (even if empty).
- `1` — user input validation (unknown user, league mismatch, bad slot/risk).
- `2` — runtime (Sleeper down, snapshot missing, schema mismatch).

## Quality gates

```bash
uv run ruff check
uv run mypy
uv run lint-imports
uv run pytest
```

## Layout

```
src/decision_engine/
  types.py                      # pydantic models
  config/                       # CLI/env resolution
  providers/sleeper.py          # Sleeper response shape validation
  clients/
    http.py                     # httpx + retry/backoff
    snapshot_reader.py          # latest snapshot loader
  core/
    eligibility.py              # slot eligibility map
    league_fetch.py             # PRD 2.1 resolution flow
    pipeline.py                 # snapshot + league -> scored candidates
    scoring/
      protocol.py               # ScoreFn / ScoreModelFactory protocols
      naive.py                  # PRD 2.2 reference impl
      __init__.py               # MODELS registry — wire new models here
  entrypoint.py                 # typer CLI

tests/
  unit/                         # pure unit tests with fakes
```

Layering enforced by `import-linter`. See `AGENTS.md`.
