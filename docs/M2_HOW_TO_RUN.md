# Milestone 2 — How to run it & how it's implemented

This is a hand-off note for reviewing the M2 (decision engine) work.
The full PRD set is at
[`docs/product-specs/milestone-2-decision-engine.md`](product-specs/milestone-2-decision-engine.md);
this doc is the shorter "what to look at and how to run it" map.

---

## 1. How to run

### Prerequisites

- Python 3.13 (driven by `uv`; the `.python-version` files pin it).
- `uv` installed.
- A snapshot on disk under `data/snapshots/<YYYY-MM-DD>/`. If you don't
  have one, run M1's loader first:

  ```bash
  cd services/stats-loader
  uv sync --extra dev
  uv run stats-loader update
  ```

### Install + first run

```bash
cd services/decision-engine
uv sync --extra dev

# Help — confirms the CLI wired up.
uv run decide --help

# Real run against your league. Replace user / league id.
uv run decide --user cole --league 1234567890 --slot FLEX --risk 0.3
```

The CLI prints a header (snapshot path, league, user, slot/risk/pool/model),
then a sorted table:

```
Snapshot: /…/data/snapshots/2026-06-12  (season 2026, week 3)
League:   "Sunday Funday" (1234567890), scoring: PPR (rec=1.0)
User:     cole (user_id 987654321)
Slot:     FLEX  Risk: 0.30  Pool: roster  Model: naive

Rank  Player                 Team Pos    Mean    Var   Score  Notes
   1  Justin Jefferson       MIN  WR     18.4    4.1   17.58
   2  Bijan Robinson         ATL  RB     14.2    3.5   13.50
   ...
```

### Common variants

```bash
# Waiver-wire WRs with a YOLO risk profile.
uv run decide --user cole --league 123 --slot WR --risk 0.9 --pool waivers

# Roster + waivers (excludes other owners' rostered players).
uv run decide --user cole --league 123 --slot FLEX --pool both

# Replay a fixed week (skips the live /v1/state/nfl call).
uv run decide --user cole --league 123 --slot QB --season 2024 --week 3

# Boost / penalise specific NFL teams (±10% multiplier).
uv run decide --user cole --league 123 --slot FLEX \
              --prefer-team DET --avoid-team CHI

# Crank up logging to see what's being fetched / loaded.
uv run decide --user cole --league 123 --slot FLEX --log-level INFO
```

### Quality gates

These are the same gates CI would run:

```bash
cd services/decision-engine
uv run pytest          # 51 unit tests
uv run ruff check      # lint
uv run mypy            # strict types
uv run lint-imports    # layered-architecture contract
```

### Exit codes (matches PRD 2.3)

| Code | Meaning |
| -- | -- |
| `0` | Printed a ranked table (length 0 is fine — empty pool is not an error). |
| `1` | User input error: unknown username, league mismatch, bad/unknown slot, bad risk, unknown model. |
| `2` | Runtime error: snapshot missing or malformed, Sleeper down, schema drift. |

---

## 2. How it's implemented (high level)

### Where the work landed

A new sibling service, `services/decision-engine/`. Layout mirrors
`stats-loader/` so the two services feel identical to navigate:

```
services/decision-engine/
  pyproject.toml                  # uv, ruff, mypy, import-linter, pytest config
  README.md
  AGENTS.md                       # per-service agent rules
  src/decision_engine/
    types.py                      # all pydantic models — frozen
    config/
      settings.py                 # CLI flag + env resolution; range validation
    providers/
      sleeper.py                  # shape validation of Sleeper responses
    clients/
      http.py                     # httpx wrapper, 3 attempts on 429/5xx
      snapshot_reader.py          # reads the lex-latest data/snapshots/<date>/
    core/
      eligibility.py              # slot eligibility map (FLEX, SUPER_FLEX, …)
      league_fetch.py             # PRD 2.1 resolution flow
      scoring/
        protocol.py               # ScoreFn / ScoreModelFactory protocols
        naive.py                  # PRD 2.2 reference implementation
        __init__.py               # MODELS registry — wire smarter models here
      pipeline.py                 # snapshot + league fetch -> ranked candidates
    entrypoint.py                 # typer CLI; rendering; exit-code mapping
  tests/unit/                     # 51 tests with in-memory fakes
```

### Layering — `types → config → providers → clients → core → entrypoint`

Identical to `stats-loader`. Enforced by `import-linter` (contract in
`pyproject.toml`). The whole `core/` package is pure: it accepts an
`HttpClient` and a `SnapshotReader` by parameter and never constructs
them, which is what makes the pipeline trivial to unit test with fakes
(see `tests/unit/fakes.py`).

### The pipeline in one paragraph

`core/pipeline.run` (1) loads the latest snapshot from disk via
`FilesystemSnapshotReader`; (2) resolves NFL state (or uses
`--season`/`--week` override); (3) walks the PRD 2.1 league-fetch flow
to get the user, the league settings, all rosters, and the user's
roster; (4) builds a candidate pool based on `--pool`
(`roster`/`waivers`/`both`); (5) filters that pool to slot-eligible
players via `core/eligibility.py`; (6) hands each candidate's
`Player`, weekly stats history, league `scoring_settings`, and `risk`
to the chosen scoring model; (7) applies optional `--prefer-team` /
`--avoid-team` ±10% multipliers in the *pipeline* (not the model, per
PRD 2.2 §7); (8) sorts by final score desc and returns the top
`--limit`.

### Scoring model — naive baseline (PRD 2.2)

Lives in `core/scoring/naive.py`. The model is a *factory*:
`build(snapshot) -> ScoreFn`. The factory step lets us precompute
position-bucket priors from the snapshot once; the returned `ScoreFn`
matches the documented 4-arg signature from PRD 2.2
(`player, stats_history, league_scoring, risk`). Naive logic:

1. **Per-week points** — multiply league scoring weights against stat
   codes. Missing codes contribute zero.
2. **Sample window** — ≥3 this-season weeks: use this-season only.
   Otherwise pad with prior-season per-week points until we hit 4
   samples or run out. Zero data: mean 0, variance 5.0, confidence low,
   note "no historical data".
3. **Mean** = arithmetic.
4. **Variance** = sample stddev with Bessel correction. 1-sample case
   falls back to position-bucket stddev computed from prior-season
   per-game points; failing that, a fixed constant (4.0).
5. **Confidence** = `high` (≥4 this-season weeks) / `medium` (1–3) /
   `low` (0).
6. **Risk-adjusted score** = `mean + (risk - 0.5) * 2 * variance`.
   `risk=0` → mean − stddev; `risk=1` → mean + stddev.

### Prior-season handling

Sleeper's prior-season endpoint returns season *totals*, not
per-week. The pipeline divides by `gp` to synthesise a single
"per-game" sample and appends it to the player's history. The naive
model treats it as another week in the padded sample window. Players
without `gp` are skipped (quarantine over drop).

### Plug point — adding a smarter model

Two files touched:

1. New sibling: `services/decision-engine/src/decision_engine/core/scoring/your_model.py`
   exposing `build(snapshot) -> ScoreFn`.
2. Register it: add `"your_model": your_model.build` to `MODELS` in
   `core/scoring/__init__.py`.

The CLI selects via `--model your_model`. Zero changes to
`core/pipeline.py` or `entrypoint.py`. This is the PRD 2.2 success
criterion verbatim.

### Slot eligibility

`core/eligibility.py` is the canonical map for slot → eligible
`fantasy_positions`. It mirrors the table in
[`docs/references/fantasy-glossary.md`](references/fantasy-glossary.md).
Bench/IR/taxi error with "not selectable". Unknown slots error with
"add it to the flex map".

### Failure modes — exit codes

The CLI catches a small set of typed exceptions and maps them to exit
codes (see `entrypoint.py` and PRD 2.3):

| Exception | Where raised | Exit |
| -- | -- | -- |
| `ValueError` from `resolve_settings` | bad `--risk`, empty `--user`, etc. | 1 |
| `UnsupportedSlotError` | `core/eligibility.py` | 1 |
| `UnknownModelError` | `core/scoring/__init__.py` | 1 |
| `UserInputError` | `core/league_fetch.py` (unknown user, league mismatch) | 1 |
| `SnapshotMissingError` | `clients/snapshot_reader.py` | 2 |
| `SnapshotSchemaError` | newer schema_version or corrupt file | 2 |
| `SchemaError` from `providers/sleeper.py` | Sleeper response drift | 2 |
| `HttpError` | Sleeper down / non-retryable 4xx | 2 |

### Tests — 51 unit tests, no network

`tests/unit/fakes.py` ships a `FakeHttp` (routes dict → payloads or
exceptions) and a `FakeSnapshotReader` (in-memory `SnapshotData`).
Coverage breakdown:

- `test_config.py` — flag/env resolution and range validation.
- `test_eligibility.py` — slot map, multi-position eligibility,
  bench/unknown errors.
- `test_providers_sleeper.py` — happy paths + quarantine over drop for
  bad list entries / non-numeric scoring weights.
- `test_snapshot_reader.py` — lex-sort, `-2` suffix, `.tmp-` ignore,
  newer schema rejected, missing weekly file aborts, prior-season
  bootstrap.
- `test_scoring_naive.py` — risk=0/0.5/1 worked examples, sample
  window selection, confidence buckets, position-prior fallback,
  unknown stat codes contributing zero.
- `test_pipeline.py` — end-to-end with fakes: roster/waivers/both pool
  composition, sorted output, prefer/avoid multipliers, unknown-user
  surfacing, league mismatch listing, BN-slot rejection, `--limit`
  capping, state-override skipping the live `/v1/state/nfl` call.

---

## 3. Reading order for code review

If you want to grok this in ~20 minutes:

1. `services/decision-engine/AGENTS.md` and the new
   `services/decision-engine/README.md`.
2. `src/decision_engine/types.py` — the data shapes the rest of the
   code passes around.
3. `src/decision_engine/core/pipeline.py` — the whole story in one
   file: snapshot → league fetch → score → rank.
4. `src/decision_engine/core/scoring/naive.py` — the algorithm the
   buddies will rewrite.
5. `src/decision_engine/core/eligibility.py` — the slot map.
6. `src/decision_engine/entrypoint.py` — CLI wiring + exit-code
   mapping + table rendering.
7. `tests/unit/test_pipeline.py` — proves the integration works
   end-to-end without touching the network.

Then peek at `clients/snapshot_reader.py`, `providers/sleeper.py`,
and `clients/http.py` if you want to verify the I/O boundary.
