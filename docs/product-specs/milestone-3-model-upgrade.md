Last updated: 2026-07-14

# PRD: Model upgrade — close the gap to the median human (milestone 3 master)

This is the **why / scope** for milestone 3. The how lives in the
implementation PRDs:

- [3.1 — Projections plumbing + availability gate](milestone-3/3.1-projections-and-availability-gate.md)
- [3.2 — Blend model](milestone-3/3.2-blend-model.md)
- [3.3 — Lineup assembly](milestone-3/3.3-lineup-assembly.md)
- [3.4 — Eval attribution + ship gate](milestone-3/3.4-eval-attribution-and-ship-gate.md)

---

## 1. Context

The 100-league eval (July 2026, `evals/`) settled the "is footballguys
just good?" question: **no.** The seed-league human is the 40th
percentile of 99 sampled managers. Every model loses to ~90% of
humans:

| model   | beats human (leagues) | weekly win rate | avg margin/season | lineup efficiency |
| ------- | --------------------- | --------------- | ----------------- | ----------------- |
| naive   | 9/99                   | 26.9%           | −163 pts          | 78.8%             |
| context | 10/99                  | 27.9%           | −152 pts          | 79.2%             |
| gbt     | 9/99                   | 28.0%           | −158 pts          | 79.0%             |
| humans  | —                      | —               | —                 | **84.9%**         |

Where the evidence points (all reproducible via
`evals/aggregate.py`; analysis in PR #8):

1. **Every model over-predicts its own lineup by 13–22 pts/week**
   (naive predicts 141 for lineups that score 119). That is the
   signature of *starting players who don't play* — injured,
   inactive, demoted. Models only see trailing `weekly_stats`; a
   Wednesday injury is invisible.
2. **The human edge is news, not projection skill.** Models lose
   worst in weeks 4–8 (−15 to −17/wk) and *beat* humans in weeks
   17–18, when managers check out (human vs-perfect decays from
   −17/wk to −37/wk late; models hold steady at ~−30).
3. **Superflex leagues are ~55 pts/season worse than standard**
   (−173 to −191 vs −127 to −137). 54 of 100 sampled leagues are
   superflex.
4. **GBT isn't earning its complexity** — best weekly win rate by a
   hair, most blowout weeks (34.1% of losses by >15), worst median
   margin. Context (ridge) is the best backbone.

Meanwhile the snapshot store already contains the missing signal:
`projections_week_<W>.json` (Sleeper's own weekly, stat-level,
pre-kickoff projections — availability, depth charts, matchup) exists
for every week of every season 2021–2025 and **no model reads it**.

## 2. Goals

Close the lineup-efficiency gap to the median human. Humans leave
−22 pts/wk vs perfect hindsight from the same rosters; our models
leave −31. The work splits into:

### 3.1 — Projections plumbing + availability gate

Load the week-W projection tables into `SnapshotData`, extend the
leakage contract ("week W sees stats < W **and projections ≤ W**"),
and add a model-agnostic pipeline gate: a player with no week-W
projection entry is not startable. Kills the ghost-start bias for
*every* model, including naive.

### 3.2 — Blend model

New registry model `blend` = context backbone + the week-W Sleeper
projection as a feature/prior, shrinking toward the projection when
history is thin (weeks 1–3, breakouts, rookies) — the exact regimes
where trailing means bleed.

### 3.3 — Lineup assembly

Replace the greedy in-`roster_positions`-order slot fill with optimal
assignment over predicted points (the bitmask DP that
`perfect_lineup_total` already uses). Fixes the superflex penalty
where a QB burned early in a flex strands a slot.

### 3.4 — Eval attribution + ship gate

Persist per-slot picks in eval results so lost points are
attributable ("started a ghost", "benched the human's best"), and
define the promotion bar a new model must clear on the frozen
100-league sample before it becomes the web default.

## 3. Success metrics

Measured on the frozen 100-league 2025 sample (`--rng-seed 42`),
all models through the same replay:

- **Primary:** `blend` weekly win rate vs human ≥ 40% (context today:
  27.9%) and mean season margin better than −75 (today: −152).
- **Bias:** mean lineup over-prediction ≤ +5 pts/wk (today: +13 to +22).
- **Superflex:** margin gap between superflex and standard leagues
  ≤ 20 pts/season (today: ~55).
- **No regressions:** `scripts/backtest-models.py` MAE for `blend` ≤
  context's; seed-league dashboard totals unchanged for existing
  models (they are untouched by 3.2, only 3.1's gate and 3.3's
  assembly may move them — those shifts must be improvements).

## 4. Out of scope

- Waiver/pickup strategy, trades, drafting — this is start/sit only.
- New external data sources (weather, Vegas lines, news APIs). The
  Sleeper projection already embeds most of that; we prove the value
  of data we have before buying more.
- IDP positions, best-ball formats (excluded by the eval sampler).
- Retiring GBT. It stays in the registry as a comparison line; we
  just stop investing in it.

## 5. Sequencing & risk

Order: 3.1 → 3.4 → 3.2 → 3.3. The gate (3.1) is small and lifts all
models; attribution (3.4) makes the remaining loss visible before we
tune 3.2 against it; assembly (3.3) is independent and last because
its effect is concentrated in superflex leagues.

**Key risk — projection back-fill leakage.** We assume Sleeper's
historical projections are the pre-kickoff values, not recomputed
after the fact. 3.1 includes a validation step (projection MAE vs
naive MAE on the backtest) — if projections look implausibly accurate
(MAE far below any honest forecaster), treat them as leaked and stop.
