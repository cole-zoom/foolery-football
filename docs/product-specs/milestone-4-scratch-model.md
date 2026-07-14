Last updated: 2026-07-14

# PRD: Scratch model — a sleeperless blend (milestone 4)

Milestone 3 closed the gap to humans, but its winning model (`blend`)
takes its point forecast directly from Sleeper's published weekly
projection. That's the right engineering call and the wrong school
project. This milestone builds `scratch`: a forecast constructed
entirely from our own archive (box scores, schedule), with Sleeper's
projection kept only as the reference ceiling it has to be measured
against.

## What Sleeper's edge decomposes into

1. **Availability / role news** (injuries, depth-chart moves) — not
   reconstructable from box scores; our archive has no historical
   injury reports (`players.json` injury fields are snapshot-time state
   — using them in replay is leakage).
2. **Opponent adjustment** — buildable: trailing fantasy points allowed
   by each defense to each position, weeks < W only.
3. **Volume forecasting** — buildable: recency-weighted usage
   (targets, carries, attempts) is far more stable than yardage/TDs.
4. **Game context** — buildable: home/away from the schedule.

`scratch` builds 2–4. For 1, the eval measures **both** worlds:

- **Run A (gate on):** scratch scores its own points, but shares the
  model-agnostic Sleeper availability gate (PRD 3.1) with every other
  model — framed as reading the injury report, not copying a forecast.
- **Run B (heuristic gate):** fully sleeper-free — availability is
  inferred from our own data ("played in his team's most recent
  completed game"). The A−B difference *is* the measured value of real
  injury news.

## Model

Same `ScoreFn` factory contract, registered as `"scratch"`. Context's
skeleton (walk-forward training inside the factory, lazy per-league
ridge fits, naive's spread/confidence/risk from `common.py`) with a
wider feature set and wider position coverage:

- **Positions fitted:** QB, RB, WR, TE (context covers only RB/WR/TE).
  K and DEF: opponent-adjusted recency-weighted mean (no regression —
  too little signal).
- **Features** (per player, predicting week W from weeks < W):
  1. recency-weighted mean points (exponential, half-life ~4 wks),
  2. flat sample mean (naive's — anchors early season),
  3. weekly stddev,
  4. recency-weighted opportunity volume (targets + carries; pass
     attempts + carries for QB),
  5. target-share trend (context's feature, 0 for QB),
  6. opponent-allowed ratio: points the week-W opponent has allowed to
     the position per game (shrunk toward the league mean), divided by
     the league mean,
  7. home flag.
- **Target:** week-W fantasy points under the league's scoring dict.
- Defensive "points allowed" tables are precomputed league-agnostically
  as per-(defense, position, week) stat sums (fantasy points are linear
  in stats), scored lazily under each league's rules.
- Target week resolution: `max(weeks_included) + 1` of the trimmed
  snapshot (the pipeline trims to < W). Week 1 has no current-season
  weeks → prior-season fallback history, opponent ratio 1.0.

Rookies with zero history stay at naive's zero/low fallback — without
projections there is no honest signal; documented limitation.

## Availability heuristic (run B)

New `DecideRequest.availability` knob: `"sleeper"` (default, PRD 3.1
gate) | `"heuristic"` | `"news"` | `"none"`. Heuristic: startable iff
the player recorded a stat row in his team's most recent completed
game (bye weeks skipped via the schedule); no completed games yet →
startable. Model-agnostic, same quarantine-over-drop conventions as
the other pool filters.

## Free injury news (run C)

`"news"` = the heuristic plus the official NFL injury report: players
designated **Out or Doubtful** for week W are benched (Questionable
plays ~75% of the time and stays startable). The report is the
league's own pre-kickoff publication, archived by nflverse (free — see
[external-data.md](../references/external-data.md)), joined to Sleeper
IDs via the dynastyprocess crosswalk at fetch time
(`scripts/fetch-injuries.py` → `injuries.json` per season →
`SnapshotData.weekly_injuries`, trimmed to ≤ W like projections).
Run C measures how much of the A−B gap free public injury news
recovers; the remainder is Sleeper's private signal (healthy
scratches, depth-chart demotions, game-day inactives).

## Acceptance

- Backtest (2025, weeks 4–18): scratch startable-MAE strictly better
  than context; report the remaining gap to `sleeper`.
- Frozen 100-league eval, run A: scratch beats context on weekly win
  rate and mean margin. Target: mean margin > 0 (beats the average
  human with our own forecast).
- Run B reported alongside: the honest fully-sleeper-free number, and
  the quantified cost of losing real injury news.
- Blend stays the default model; scratch ships as a registry/picker
  option and the writeup's centerpiece.
