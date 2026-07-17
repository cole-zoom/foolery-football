# services/decision-engine

The CLI: given a Sleeper user + league + slot + risk knob, fetch
live league context, read the latest local snapshot, score
eligible players, and print them ranked.

## Inputs

- `--user` — Sleeper username (we resolve to `user_id` via API).
- `--league` — Sleeper league ID. Must be one of the user's active leagues.
- `--slot` — Position slot to fill (`QB`, `RB`, `WR`, `TE`, `FLEX`, or any custom slot defined by the league's `roster_positions`).
- `--risk` — 0.0 (max safety) to 1.0 (max gamble). Default 0.5.
- `--prefer-team <TEAM>` / `--avoid-team <TEAM>` — optional soft multipliers (±10%).
- `--pool` — `roster` (default — only players on the user's team), `waivers` (only free agents in the league), or `both`.
- `--limit <N>` — cap the printed table (default 10).

## What it does

1. Resolve username → `user_id` (Sleeper).
2. Fetch league settings, rosters, and the current week's matchups
   (live, not snapshotted).
3. Read the latest `data/snapshots/<date>/` folder for player
   metadata, weekly stats, and projections.
4. Filter the candidate pool to `--slot`-eligible players in
   `--pool`, dropping players whose team has no game in the target
   week (bye, per the snapshot's season schedule; weeks the schedule
   doesn't cover skip the filter) and players Sleeper's week-W
   projection table doesn't expect to play (the availability gate,
   [PRD 3.1](../product-specs/milestone-3/3.1-projections-and-availability-gate.md);
   weeks with no projection table skip the filter).
5. For each candidate, compute:
   - `projection_mean` — see [scoring model PRD](../product-specs/milestone-2/2.2-scoring-model.md).
   - `projection_variance` — stddev across prior weeks.
   - `score = mean + (risk - 0.5) * 2 * variance`, then apply team
     preference multipliers.
6. Print players sorted by `score` descending, with `mean`,
   `variance`, and any preference adjustments shown.

## Compares like with like

- QBs are scored only against other QBs.
- RBs, WRs, TEs are each scored within their own pool by default.
- The `FLEX` slot (or any multi-eligible slot like `WRRB_FLEX`,
  `SUPER_FLEX`) pulls from all positions eligible for that slot per
  the league's `roster_positions` setting. See the glossary at
  [`docs/references/fantasy-glossary.md`](../references/fantasy-glossary.md)
  for the eligibility map.

## Layering

Same as stats-loader: `types → config → providers → clients → core → entrypoint`.

`core.scoring` is where the buddies will iterate. It exposes:

```python
def score_player(
    player: Player,
    stats_history: list[WeeklyStats],
    league_scoring: ScoringSettings,
    risk: float,
) -> PlayerScore: ...
```

The naive v1 implementation lives in `core.scoring.naive`. Swapping
in a smarter model means adding a sibling module and selecting
between them via a config flag — no edits to `core/pipeline.py` or
the CLI. Registered today: `naive`, `context` (ridge regression),
`gbt` (boosted trees), `scratch` (Sleeper-free rebuild — milestone 4),
`blend` (Sleeper's week-W projection as the mean, context's spread —
[PRD 3.2](../product-specs/milestone-3/3.2-blend-model.md)).

Production is pinned to `blend`: the API no longer accepts a `?model=`
param (`PROD_MODEL` in `services/api/src/api/config.py`) and the web
app has no model picker. The rest of the registry is the evaluation
ladder — reachable via the engine CLI's `--model` and `evals/` so the
selection evidence stays reproducible.

Multi-slot lineups (the `/decisions` router and week replays) assign
players to slots optimally over predicted points via the bitmask DP in
`core/lineup.py` ([PRD 3.3](../product-specs/milestone-3/3.3-lineup-assembly.md))
— the greedy in-league-order fill mishandled superflex leagues.

## Running locally

```bash
cd services/decision-engine
uv sync --extra dev
uv run pytest
uv run ruff check
uv run lint-imports

# Score FLEX candidates on your roster, moderately safe.
uv run decide --user cole --league 1234567890 --slot FLEX --risk 0.3

# Score waiver-wire WRs with a YOLO risk profile.
uv run decide --user cole --league 1234567890 --slot WR --risk 0.9 \
              --pool waivers
```

## Scope of this service

Today: CLI output to stdout. Live Sleeper league fetch + snapshot
read + naive scoring.

Out of scope:

- Web UI.
- Persistent user preferences or saved sessions.
- Recommending *combinations* of players (start/sit pairs, trade
  proposals). Single-slot scoring only.
- Smarter scoring models — kept as a deliberate plug point for the
  team to iterate on.
