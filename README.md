# Fantasy Football Decision Maker

An intelligent decision support system (IDSS) for the weekly **start/sit
decision** in fantasy football. The stakeholder is a Sleeper league
manager who, every week, must choose which of their rostered players to
start at each lineup slot; a wrong call costs real points and, over a
season, playoff position. The system scores every eligible player under
the manager's own league scoring rules, recommends a full lineup, and —
critically — lets the manager interrogate and steer that recommendation
live.

**Live app:** <https://foolery-football.vercel.app> · enter a Sleeper
username and league to try it (e.g. user `ben`).

## Why an IDSS and not a report

Two properties make this a decision *support* system rather than a
scheduled script and an emailed ranking:

- **The user's judgement is load-bearing.** Risk appetite, model choice,
  candidate pool, team preferences, and per-slot overrides ("I'm
  starting my guy regardless") all change the recommended lineup at
  request time. The model can't learn these — they live in the user's
  head — so the system exposes them as live controls.
- **The data never sits still.** New stats land weekly, injuries and
  roster moves land daily, and every league scores differently. The
  model retrains from each new snapshot automatically (see
  [Models](#models)), and league context is fetched live per request.

## Architecture

```
Sleeper API ──▶ stats-loader ──▶ data/seasons/<year>/ ──rsync──▶ GCS bucket
 (weekly)         (batch)          (local snapshot)                  │
                                                                     ▼
React web app (Vercel) ──▶ FastAPI api (Cloud Run) ──▶ decision-engine (library)
                                   │                        ▲
                                   └── live league fetch ───┘   also a standalone CLI
```

- `services/stats-loader/` — batch job that snapshots Sleeper player,
  stats, projections, and schedule data into an immutable per-season
  folder.
- `services/decision-engine/` — the scoring core and a standalone CLI
  (`decide`). Pure functions over typed data; models plug in via a
  registry (see below).
- `services/app/` — interactive terminal app (`ffdm`) and the session
  layer the API reuses, so CLI and web answers always agree.
- `services/api/` — FastAPI service the web app talks to. Reads
  snapshots from GCS in production, local disk in dev.
- `web/` — React + Vite frontend.
- `infra/` — Terraform for the GCS snapshot bucket.

Full details: [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`docs/index.md`](docs/index.md).

## Data

- **Source:** the public [Sleeper API](https://docs.sleeper.com/) —
  `/v1/players/nfl`, `/v1/stats/nfl/regular/<season>/<week>`,
  `/v1/projections/...`, `/schedule/nfl/regular/<season>`, plus live
  league/roster/scoring endpoints at request time.
- **Structure & volume:** one snapshot folder per season — a ~12k-player
  registry, one stat file per completed week (~2k player-weeks each,
  flat stat-code → value maps), the season schedule, and a manifest that
  acts as the commit marker. Seasons 2021–2026 are snapshotted (~40 MB
  on disk, ~150 MB parsed).
- **Refresh:** re-running the loader replaces a season's folder
  atomically; `gsutil rsync` pushes it to the bucket, and the deployed
  API picks it up automatically (its cache keys on the manifest's GCS
  generation). During the season this is a weekly run.
- **Known limitations, stated honestly:** stats are attributed to a
  player's *current* team, so mid-season trades blur team-level
  features; players who didn't play produce no row (the models learn
  "points when playing"); early-season weeks give every model thin
  samples; and Sleeper's stat feed is undocumented, so the loader
  validates shape on every fetch and refuses to write a snapshot it
  doesn't recognise.

## Models

Scoring models are plug-and-play: a model is one module in
`services/decision-engine/src/decision_engine/core/scoring/` exposing a
`build(snapshot) -> ScoreFn` factory, registered in one line in
`MODELS`. The CLI (`--model`) and API (`?model=`) select by name with no
pipeline edits.

| Model | What it does |
| -- | -- |
| `naive` | Rolling mean of recent weekly points; sample stddev as spread. The permanent control — every new model must beat it in backtest. |
| `context` | Per-position ridge regression (RB/WR/TE) over naive's mean/stddev **plus** target volume and target-share trend. QB/K/DEF fall back to naive until their own features land. |

Design decisions worth knowing:

- **League-agnostic by construction.** The prediction target is fantasy
  points under *the requesting league's* scoring settings, so the same
  model serves PPR, standard, and custom leagues without retraining
  infrastructure.
- **Training is walk-forward inside the factory.** For each completed
  week W, features come only from weeks strictly before W. That is
  leakage-safe by the same rule the replay harness uses, and it means
  *retraining is simply loading a fresher snapshot* — no artifacts, no
  training jobs, no extra dependencies.
- **Both risk handling and uncertainty are explicit.** Every score is a
  mean, a spread, and a confidence tier; the user's risk slider maps
  onto `mean + (risk − 0.5) · 2 · spread`.
- **Bye weeks are filtered structurally, not predicted.** The season
  schedule ships in every snapshot, so any player whose team has no
  game in the target week is dropped from the candidate pool before
  scoring — live weeks and replays alike. Injury scratches remain the
  models' blind spot; the schedule can't see those.
- **Ship gate.** `scripts/backtest-models.py` replays a season
  week-by-week (each week predicted from strictly-prior data) and
  compares models on MAE, startable-player MAE, and top-K precision.
  On the 2025 replay the context model improves startable-player MAE at
  every regressed position (e.g. TE 6.18 → 5.76 PPR points); its known
  trade-off is worse MAE on deep-bench players that never enter a
  lineup decision.

```bash
uv run --project services/decision-engine \
    python scripts/backtest-models.py --season 2025 --weeks 4-18
```

## User interface

Every control below changes the model's output, and therefore the
recommended lineup — none is cosmetic:

- **Risk slider** — shifts every score along its uncertainty; cautious
  managers get high-floor lineups, trailing managers get high-ceiling
  ones.
- **Model picker** — context regression vs. naive baseline, re-scored
  live. The two models genuinely disagree (different WR1/FLEX picks),
  and the UI shows which.
- **Week & season pickers** — replay any historical week; the model
  sees only data available before that week.
- **Candidate pool** — roster only, waivers only, or both (turns the
  start/sit tool into a pickup scout).
- **Team prefer/avoid** — a ±10% thumb on the scale for teams the user
  believes in or not.
- **Per-slot pins** — override any recommendation; totals recompute
  around the override.
- **Model vs. you (hindsight) view** — for any completed week, the
  lineup the model would have fielded (replayed leakage-safe) beside
  the lineup you actually started, both scored by real results. Comes
  with the model's report card: per-player predicted-vs-actual errors,
  MAE, signed bias, and the perfect-hindsight lineup ("points left on
  the bench"). The week-W roster comes from Sleeper's matchup archive,
  so mid-season trades don't leak into the replay. Honors the candidate
  pool knob: with waivers in play it shows what the model could have
  fielded off that week's wire — free agency judged by the week-W
  matchup rosters, not today's.

Decision-tied readouts: each slot shows **MATCH** (your starter is
already optimal) or **SWAP +N** (projected points gained by benching
your current starter for the recommendation), the projection card totals
the model's edge over your current lineup, and every recommendation
carries the model's own explanation (`context: WR regression (n=2031)`
or its honest fallback reason) plus a confidence tier.

## Running locally

Prereqs: Python 3.13+, [uv](https://docs.astral.sh/uv/), Node 20+ with
pnpm. No API keys and no Sleeper account needed — Sleeper's API is
public, and the examples below use a real public league
(`footballguys` / "The Party League", 2025).

```bash
# 1. Snapshot data. In the offseason pin the last completed season;
#    during the season a bare `stats-loader update` snapshots the live one.
cd services/stats-loader && uv sync --all-extras
uv run stats-loader update --season 2025 --week 18

# 2a. CLI: ranked candidates for one slot. --season/--week replay a
#     completed week; omit both during the season for the live week.
cd ../decision-engine && uv sync --all-extras
uv run decide --user footballguys --league 1182163805001936896 \
              --slot FLEX --risk 0.3 --season 2025 --week 10

# 2b. Or the full stack: API + web.
cd ../api && uv sync --all-extras
uv run ffdm-api                     # http://127.0.0.1:8000, local snapshots

cd ../../web && pnpm install
pnpm dev                            # http://localhost:5173, proxies /api
```

In the web form, enter username `footballguys` and pick "The Party
League" from the dropdown the form fills in. The API downloads any
missing season snapshot on first request (the bare `decide` CLI does
not — hence step 1). Tip: replay week 10 rather than 18 — week 18
rosters are thinned by resting starters.

Tests and lint (same commands CI runs per service):

```bash
cd services/<service> && uv run pytest && uv run ruff check src tests
cd services/decision-engine && uv run lint-imports   # layering contract
cd web && pnpm build                                 # typecheck + build
```

## Operationalization

- **Hosting:** the API is a container on **Google Cloud Run**
  (`cloudbuild.yaml` builds and deploys on every push to `main`); the
  web app deploys on **Vercel**, which proxies `/api/*` to Cloud Run.
- **Data continuity:** snapshots live in a GCS bucket (Terraform in
  `infra/`). A weekly `stats-loader` run + `gsutil rsync` during the
  season is the entire pipeline; the API hot-reloads new snapshots
  without a deploy.
- **Model updates:** models refit in-process from whatever snapshot they
  are handed, so a data refresh *is* the retrain. Code changes to models
  ship through the normal PR → CI → Cloud Run deploy path, gated on the
  backtest beating `naive`.
- **Footprint:** one Cloud Run service, one GCS bucket, one Vercel
  project. No databases, no schedulers, no GPU — the heaviest compute is
  a closed-form regression fit measured in milliseconds.

## Working on this repo

- [`AGENTS.md`](AGENTS.md) — how agents should work in this repo (read
  first if you're an agent).
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — pipeline, storage contract,
  service layering.
- [`docs/pdfs/`](docs/pdfs/) *(local only, gitignored)* — design briefs
  and course specification.

## License

TBD.
