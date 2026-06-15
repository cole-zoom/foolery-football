Last updated: 2026-06-12

# Sleeper API endpoint inventory

The official Sleeper docs at https://docs.sleeper.com/ omit several
endpoints we depend on. This file is the **single source of truth**
for what we call. If you discover a new working endpoint, add it
here with a one-line description of what it returns and what we'd
use it for. If a known one stops working, note the date and the
failure mode.

All endpoints are public, read-only, and require no auth. Base URL:
`https://api.sleeper.app`. Rate limit per Sleeper's docs: stay
under 1000 calls/minute.

## Documented (per Sleeper docs)

| Method | Path | Returns / use |
| -- | -- | -- |
| GET | `/v1/user/<username_or_id>` | User object: `user_id`, `username`, `display_name`. We resolve usernames here. |
| GET | `/v1/user/<user_id>/leagues/nfl/<season>` | List of leagues the user is in for that season. |
| GET | `/v1/league/<league_id>` | League settings: `roster_positions`, `scoring_settings`, `season`, `status`, etc. |
| GET | `/v1/league/<league_id>/rosters` | Array of rosters. Each has `owner_id`, `roster_id`, `players` (list of player_ids), `starters`. |
| GET | `/v1/league/<league_id>/users` | Array of users in the league with `display_name` and `user_id`. |
| GET | `/v1/league/<league_id>/matchups/<week>` | Array of matchup entries with `points`, `starters`, `players`, `roster_id`. |
| GET | `/v1/players/nfl` | Object keyed by `player_id`. ~5MB. Sleeper requests ≤1 fetch/day. |
| GET | `/v1/state/nfl` | Current season + week. Our authoritative source for "what week is it right now?" |

## Undocumented but working

These are not in the official docs. Used at your own risk; if they
break, we will need to revisit and possibly switch to `nfl_data_py`
or another source. Verified working as of 2026-06-12 against the
prior NFL season.

| Method | Path | Returns / use |
| -- | -- | -- |
| GET | `/v1/stats/nfl/regular/<season>/<week>` | Object keyed by `player_id`, values are `{stat_code: number}`. Used for variance + mean. |
| GET | `/v1/projections/nfl/regular/<season>/<week>` | Same shape as stats, but Sleeper's projection. Stored as a baseline. |
| GET | `/v1/stats/nfl/regular/<season>` | Same shape, but season totals. Used to bootstrap week-1 variance. |

If a buddy with fantasy expertise knows of more endpoints (e.g. ADP,
draft picks, trending players, transactions), add them here with
their use case before integrating.

## Stat codes (partial)

Sleeper's stat / scoring keys are flat strings. The common ones:

| Code | Meaning |
| -- | -- |
| `pass_yd` | Passing yards |
| `pass_td` | Passing touchdowns |
| `pass_int` | Interceptions thrown |
| `rush_yd` | Rushing yards |
| `rush_td` | Rushing touchdowns |
| `rec` | Receptions (PPR=1.0, half=0.5, standard=0) |
| `rec_yd` | Receiving yards |
| `rec_td` | Receiving touchdowns |
| `fum_lost` | Fumbles lost |

The full code list lives in the response itself — we don't need to
enumerate. The scoring model multiplies whatever codes appear in
the league's `scoring_settings` against the same codes in the
stats response.

## Failure / rate-limit handling

- 429 → exponential backoff, up to 3 attempts.
- 5xx → exponential backoff, up to 3 attempts.
- 404 → no retry, treat as "doesn't exist."
- Other 4xx → no retry, abort with the body in the error message.

## Things we have NOT verified

- Whether stats endpoints return historical data for arbitrarily
  old past seasons (we'll check during M1 implementation).
- Whether projections exist for week 0 / preseason (assume not).
- DST / IDP scoring code names — only handled when a buddy adds them
  to a league we test against.
- Whether `/regular/` path covers playoff weeks. Probably not;
  there may be a `/post/` path we haven't tried.
