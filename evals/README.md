# Evals — many-league model benchmark

The web dashboard benchmarks the models against **one** league — the
footballguys staff league, i.e. professional fantasy analysts. This
harness replays ~100 randomly sampled Sleeper leagues (one team each)
to answer: *is that human an outlier, or do the models underperform
typical managers?*

Each (league, week, model) cell runs the same leakage-safe replay the
web comparison view uses (`decision_engine.core.replay`): the model
re-picks the lineup from the week-W matchup-archive roster seeing only
weeks < W; the human total is what the manager actually fielded; the
perfect total is the hindsight-optimal assignment.

## Pipeline

All commands run from the repo root under decision-engine's env.

```bash
# 1. Sample leagues (BFS over the Sleeper social graph from the seed league).
uv run --project services/decision-engine python evals/discover_leagues.py \
    --seed-league 1182163805001936896 --seed-user footballguys \
    --season 2025 --count 100 --rng-seed 42 --out evals/leagues_2025.json

# 2. Replay. Week-major loop, checkpoints per league — Ctrl-C safe, resumes.
uv run --project services/decision-engine python evals/run_eval.py \
    --leagues evals/leagues_2025.json --season 2025 --weeks 1-18 \
    --models naive,context,gbt

# 3. Aggregate + report.
uv run --project services/decision-engine python evals/aggregate.py --season 2025
```

Useful knobs: `run_eval.py --limit 5` (first N leagues), `--models naive`
(fast smoke pass), `--force` (recompute). `aggregate.py --min-full-weeks`
controls the "engaged humans" filtered view.

## Notes

- **Every Sleeper response is disk-cached** under `evals/cache/`
  (gitignored), throttled to ~600 req/min when live. Re-runs are fully
  offline and deterministic.
- **Qualification** (`common.qualifies`): completed 2025 NFL redraft
  league, not best-ball (no human lineup decisions), only slots the
  engine can score (no IDP), ≤ 14 starter slots (perfect-DP cap).
- **Team choice**: one seeded-random roster per league among those that
  fielded a full lineup in week 14 (cheap dead-team screen). Weeks where
  the manager left starter slots empty are *flagged*
  (`human_full_lineup`), not excluded — weak humans are part of the
  distribution being measured. The seed league's team is pinned to the
  `footballguys` user so it matches the dashboard.
- **Fairness**: per league, every series (models / human / perfect) is
  summed over the identical set of clean weeks.
- **Runtime**: GBT dominates — it refits per unique scoring config per
  week (pure-Python trees). Expect a few hours for the full
  100 × 18 × 3 run; naive-only is minutes. Checkpoints make it safe to
  stop and resume.

Results land in `evals/results/<season>/` (gitignored), reports in
`evals/reports/`. A follow-up could surface the report in `web/`.

## Frozen sample

`evals/testdata/leagues_2025.json` is the committed copy of the
100-league sample (`--rng-seed 42`). It is the ship-gate population —
never regenerate it; a drifted sample silently changes the bar. The
gitignored `evals/leagues_2025.json` working copy should be identical
(restore it from testdata if lost).

## Attribution

`run_eval.py` persists per-slot `picks` in each model cell (who the
model started vs the human, actuals, and the best eligible bench
alternative). `aggregate.py` turns those into per-model attribution:
ghost starts (started a player who didn't play), benched-the-human's-
best, and a per-losing-week decomposition into ghost points vs
ranking-error points. Old result files without `picks` still
aggregate; the attribution table just covers fewer weeks.

Offline tests: `uv run --project services/decision-engine python -m
pytest evals/test_aggregate.py`.

## Ship gate (PRD 3.4)

A model becomes the web/CLI default only when, on the frozen sample:

1. weekly win rate vs human ≥ 40% **and** mean season margin ≥ −75;
2. over-prediction bias ≤ +5 pts/wk (the `bias/wk` column);
3. backtest (`scripts/backtest-models.py`, 2025, weeks 4–18)
   startable-MAE not worse than context's;
4. seed (footballguys) league: not the worst model there.

Every model/assembly PR pastes the before/after of `aggregate.py`
(all-leagues table + attribution table) in the PR body.
