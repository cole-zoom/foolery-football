# How to re-run the 2025 season replay

The integration test
[`services/decision-engine/tests/integration/test_2025_season.py`](../services/decision-engine/tests/integration/test_2025_season.py)
walks weeks 1–17 of the 2025 NFL season, picks an ideal lineup each
week using **only data that would have been available before that
week's kickoff**, and prints projected vs actual points.

It has two modes:

| Mode | Test name | Roster source | Needs creds? |
| -- | -- | -- | -- |
| Synthetic | `test_synthetic_2025_season` | Top scorers per position, drawn from the snapshot | No |
| Real team | `test_real_team_2025_season` | Live Sleeper — your league's `matchups/<week>` endpoint, so the roster matches what you actually had that week | Yes (`DE_USER` + `DE_LEAGUE`) |

Both modes are opt-in via `DECISION_ENGINE_INTEGRATION=1`.

---

## 1. One-time setup: get a 2025 snapshot

The decision engine reads from `data/snapshots/<date>/`. The replay
needs a snapshot whose `manifest.season == 2025` with weeks 1–17
included.

If you don't have one yet:

```bash
cd services/stats-loader
uv sync --extra dev
uv run stats-loader update --season 2025 --week 19
```

Why `--week 19`? The loader treats `state.week - 1` as the most recent
completed week. `--week 19` ⇒ completed_through=18, so the loader
fetches stats + projections for **all 18 regular-season weeks of 2025**.
It also tries to fetch a "week 19 projection" — Sleeper returns 200 with
an empty/short body, which is fine.

Verify what landed:

```bash
ls /Users/coledumanski/Documents/Workspace/fantasy-football-decision-maker/data/snapshots/
# 2026-06-12   <- old (2024 data)
# 2026-06-13   <- new (2025 data, all 18 weeks)

python3 -c "
import json
with open('data/snapshots/2026-06-13/manifest.json') as f:
    m = json.load(f)
print('season:', m['season'], 'weeks:', m['weeks_included'])
"
# season: 2025 weeks: [1, 2, ..., 18]
```

The decision engine picks the **lexicographically latest** snapshot
folder, so as long as the 2025 one sorts last (it will — it's newer),
no further config is needed.

---

## 2. Re-run the synthetic mode (no Sleeper creds)

```bash
cd services/decision-engine
DECISION_ENGINE_INTEGRATION=1 \
  uv run pytest tests/integration/test_2025_season.py::test_synthetic_2025_season \
                -m integration -s
```

The `-s` flag is important — it tells pytest to **not** capture stdout
so the week-by-week table prints to your terminal.

You'll see something like:

```
=== 2025 season replay for synthetic ===
League:           'Synthetic 2025 PPR' (synthetic-league)
Roster positions: ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'K', 'DEF', 'BN', ...]
Scoring:          rec=1.0 pass_yd=0.04 rush_td=6.0 ...

Week  1   projected    0.0   actual   71.4
   QB          Josh Allen               BUF QB   proj   0.0   actual  10.4
   ...

Week  2   projected  156.8   actual  127.3
   QB          Josh Allen               BUF QB   proj  38.8   actual  11.8
   RB          Bijan Robinson           ATL RB   proj  24.4   actual  19.8
   ...

Season totals: projected 2512.0   actual 2368.3
```

Week 1 projects 0.0 because the naive model has no historical data —
the snapshot's `weeks_included` was filtered to weeks `< 1` (empty
set). Weeks 2+ use this-season weekly stats as they accumulate.

---

## 3. Re-run with YOUR team (live Sleeper)

```bash
cd services/decision-engine
DECISION_ENGINE_INTEGRATION=1 \
  DE_USER=your_sleeper_username \
  DE_LEAGUE=your_2025_league_id \
  uv run pytest tests/integration/test_2025_season.py::test_real_team_2025_season \
                -m integration -s
```

To find your league ID: log in to [sleeper.com](https://sleeper.com),
open the league, and copy the digits from the URL
(`sleeper.com/leagues/`**`1234567890`**`/team`).

What changes vs synthetic mode:

- The roster used for each week comes from
  `GET /v1/league/<id>/matchups/<week>` — that endpoint returns the
  roster as it was *at the time of that week*, so trades and
  pickups are reflected correctly.
- `roster_positions` and `scoring_settings` come from your real
  league, so PPR / half-PPR / standard / custom-bonus all work
  without code changes.

---

## 4. Re-run both at once

```bash
cd services/decision-engine
DECISION_ENGINE_INTEGRATION=1 \
  DE_USER=your_sleeper_username \
  DE_LEAGUE=your_2025_league_id \
  uv run pytest tests/integration/ -m integration -s
```

Both tests print their own table. Each one asserts the actual season
total is `> 0` — that's the only assertion, since the "right answer"
for a season replay is judgment-call territory.

---

## 5. What the test is actually doing each week

For week `W` in 1..17:

```
1. snapshot_as_of = snapshot.model_copy(weekly_stats={w: s for w, s in
                                                      snapshot.weekly_stats.items()
                                                      if w < W})
   # Filter to only weeks before W. This is the data the engine
   # "would have had" before kickoff that week.

2. score_fn = naive.build(snapshot_as_of)
   # Factory call. Precomputes position-bucket priors over whatever
   # prior-season data is in the snapshot (currently empty — see
   # "Known gaps" below).

3. For each player on the user's week-W roster:
       history = _build_history(player_id, snapshot_as_of)
       score   = score_fn(player, history, scoring, risk=0.5)

4. Greedy slot assignment:
   - Sort starting slots by restrictiveness (K/DEF before FLEX).
   - For each slot, pick the highest-scoring unused eligible player.

5. Compute actual points for each picked player using
   snapshot.weekly_stats[W] (which is NOT in snapshot_as_of — that's
   the "what really happened" view).

6. Print the lineup and the totals.
```

The greedy assignment is in the test file
(`_greedy_lineup` in `test_2025_season.py`), **not** in `core/`. The
decision engine itself is single-slot by design (PRD 2.2 §3); multi-slot
lineup optimisation is one layer up.

---

## 6. Known gaps

These are real and worth knowing about, but didn't block writing the test:

- **K / DEF score 0.0.** The synthetic mode's PPR scoring map
  (`STANDARD_PPR_SCORING` in the test) only includes passing/rushing/
  receiving stat codes. Kicker codes (`fgm`, `xpm`, `fgm_50p`) and
  defensive codes (`def_int`, `def_td`, `sack`, etc.) aren't in the
  map, so those positions always project 0. The real-team mode reads
  the league's actual `scoring_settings`, which Sleeper populates
  correctly for K/DEF — that mode handles them.

- **Week 1 projects 0 in synthetic mode.** There's no prior-season
  data in the 2025 snapshot (the loader only bootstraps prior-season
  totals when `completed_through == 0`, and we fetched with
  `--week 19`). So week 1's "as-of" snapshot is genuinely empty, and
  the naive model returns mean=0 / variance=5.0 / "no historical
  data".

  Fix would be a second loader pass that merges the prior-season
  totals into the snapshot — out of scope for this test.

- **Roster snapshot for synthetic mode is fixed.** The synthetic team
  doesn't change week to week (no trades, no waiver pickups). The
  real-team mode does change, because `/matchups/<week>` returns the
  roster as it was that week.

- **Bye weeks aren't filtered.** A player on bye shows up in the
  scored list with whatever their pre-bye mean was. The naive model
  surfaces this only implicitly — the actual-points column will read
  0.0 for that week. PRD 2.2 notes this is fair game for a smarter
  model.

---

## 7. File map

- **Test**:
  [`services/decision-engine/tests/integration/test_2025_season.py`](../services/decision-engine/tests/integration/test_2025_season.py)
- **Pytest marker**: `integration` (registered in
  `services/decision-engine/pyproject.toml`).
- **Snapshot reader**:
  `services/decision-engine/src/decision_engine/clients/snapshot_reader.py`.
- **Scoring model**:
  `services/decision-engine/src/decision_engine/core/scoring/naive.py`.
- **Snapshot location**: `data/snapshots/<YYYY-MM-DD>/`.
