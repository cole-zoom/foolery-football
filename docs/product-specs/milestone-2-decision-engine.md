Last updated: 2026-06-12

# PRD: Decision engine (milestone 2 master)

This is the **why / scope** for milestone 2. The how lives in the
implementation PRDs:

- [2.1 — Sleeper league fetch](milestone-2/2.1-sleeper-league-fetch.md)
- [2.2 — Scoring model](milestone-2/2.2-scoring-model.md)
- [2.3 — CLI output](milestone-2/2.3-cli-output.md)

---

## 1. Context

Given the local snapshot from milestone 1, the user wants to ask:
"of the players I could start (or pick up) for this slot in my
league this week, ranked by my risk preference, who should I pick?"

The user is a person playing fantasy football, but the *author* of
this repo is not. So the scoring model is intentionally
plug-and-play — the buddies who actually understand fantasy will
swap the naive baseline for something smarter, and the naive
baseline exists so they have a clean target to beat without
having to build any plumbing.

## 2. Goals

### 2.1 — Sleeper league fetch

- Resolve `--user` (Sleeper username) → `user_id`.
- Fetch `user_id`'s active leagues for the current season; require
  `--league` to match one of them. If not, list available leagues
  and exit non-zero.
- Fetch league settings (`roster_positions`, `scoring_settings`),
  rosters, users, and the current week's matchups.
- Identify the user's roster. Compute the waiver / free-agent pool
  as `{all players in snapshot} − {players rostered in this league}`.

### 2.2 — Scoring model

- Baseline naive algorithm, defined in
  [2.2 — Scoring model](milestone-2/2.2-scoring-model.md).
- Single public function:
  `score_player(player, stats_history, league_scoring, risk) → PlayerScore`.
- Future smarter implementations live as sibling modules and are
  selected via config. The naive one is the reference impl.

### 2.3 — CLI output

- `decide` command with the flags listed in
  [`docs/services/decision-engine.md`](../services/decision-engine.md).
- Prints a sorted table of candidates with score, mean, variance,
  and any applied preference adjustments.
- Exits non-zero on any fetch / scoring / snapshot-read failure.

## 3. Non-goals

- No multi-slot recommendation (start/sit pairs, trade analysis).
  Single-slot scoring only.
- No persistent user state.
- No web UI or HTTP API. CLI only.
- No "smart" model. The naive baseline is deliberately dumb so the
  buddies have a clear target to beat.

## 4. Success criteria

- Given a valid username + league + slot + risk, the CLI prints a
  ranked list of eligible players in under 2 seconds (assuming the
  snapshot is on local disk).
- Swapping the naive scoring model for a new one requires editing
  *one* config flag and adding a sibling module — no changes to
  `core/pipeline.py`, no changes to the CLI.
- Running the CLI without a snapshot present fails with a clear
  "run `stats-loader update` first" message.
- The same CLI works for a PPR league, a standard league, and a
  custom-scoring league with no code changes — because scoring
  weights are read from the league response.
