Last updated: 2026-06-12

# Fantasy football glossary

The author of this repo does not play fantasy football. Future
agents may not either. This file is the load-bearing source for
"what does that word mean in this codebase." If a domain term
appears in code, a PRD, or a Sleeper response and isn't here, add
it.

## Slots / positions

| Term | Meaning |
| -- | -- |
| **QB** | Quarterback. One per team. Throws the ball. |
| **RB** | Running back. Runs the ball; also catches passes (worth more in PPR). |
| **WR** | Wide receiver. Catches passes. |
| **TE** | Tight end. Hybrid blocker/receiver. |
| **K** | Kicker. Kicks field goals and extra points. |
| **DEF** / **DST** | Team defense / special teams. Scored as a unit per NFL team. |
| **FLEX** | A roster slot fillable by RB **or** WR **or** TE. The most common flex. |
| **WRRB_FLEX** | Flex that accepts WR or RB only (no TE). |
| **WRT_FLEX** | Same as standard FLEX (WR/RB/TE) — different leagues name it differently. |
| **SUPER_FLEX** | Accepts QB *plus* RB/WR/TE. Effectively a second QB slot. |
| **BN** | Bench. Players on the roster who don't earn points this week but can be swapped in. |
| **IR** | Injured reserve. Held off the active roster while injured. |
| **TAXI** | Practice-squad-style slot in dynasty leagues. |
| **IDP** | Individual defensive player. Some leagues score by individual defenders instead of team defense. Out of scope for v1. |

## Slot eligibility map

The decision engine uses this map to decide whether a player can
fill a slot, given the player's `fantasy_positions`.

| Slot | Eligible `fantasy_positions` |
| -- | -- |
| `QB` | `QB` |
| `RB` | `RB` |
| `WR` | `WR` |
| `TE` | `TE` |
| `K` | `K` |
| `DEF` / `DST` | `DEF` |
| `FLEX` | `RB`, `WR`, `TE` |
| `WRRB_FLEX` | `RB`, `WR` |
| `WRT_FLEX` | `RB`, `WR`, `TE` (same as FLEX, alternate name) |
| `SUPER_FLEX` | `QB`, `RB`, `WR`, `TE` |
| `BN`, `IR`, `TAXI` | not selectable as a `--slot` |

Any slot not in this map causes the CLI to abort with "unsupported
slot — add it to the flex map." This is the extension point for
leagues with non-default position scopes (e.g. defensive-only).

## Scoring formats

| Term | Meaning |
| -- | -- |
| **Standard** | Receptions worth 0 points. |
| **Half-PPR** | Each reception = 0.5 points. |
| **PPR** | Each reception = 1.0 point. |

We don't hardcode any of these. We read `league.scoring_settings`
and apply the weights as given. The format names above are useful
shorthand in conversation but never appear in code.

## Variance / risk

| Term | Meaning |
| -- | -- |
| **Mean / projection** | What we expect the player to score this week. In points. |
| **Variance** | How spread out their past scores are. Stored as a stddev in points. |
| **Risk tolerance** | User input 0.0–1.0. 0 = "I want consistency, give me the safe pick." 1 = "I'll gamble on upside." 0.5 = "score by mean only." |
| **Boom/bust** | A player with high mean *and* high variance. Risk-tolerant users prefer them. |
| **Floor** | The low end of a player's likely score (≈ mean − variance). |
| **Ceiling** | The high end (≈ mean + variance). |

## League mechanics

| Term | Meaning |
| -- | -- |
| **Waiver wire** | The pool of NFL players not on anyone's roster in the league. |
| **Free agent** | Roughly synonymous with waiver wire in casual usage. |
| **Roster** | The set of players on a user's team (starters + bench). |
| **Starters** | The subset of the roster that earns points in a given week. |
| **Matchup** | The two teams playing head-to-head in a given week. |
| **Bye week** | Each NFL team has one week off per season. Players on bye score zero. The decision engine should ideally surface this; v1 may just note it. |
| **Dynasty league** | League where rosters persist across seasons. Has taxi / IR mechanics. Not special-cased in v1. |
| **Redraft league** | League where rosters reset each season. The default assumption. |

## NFL team codes

Sleeper uses 2–3 letter team codes (e.g. `KC`, `SEA`, `LAR`, `LAC`,
`JAX`, `WAS`). These appear in player `team` fields and in
`--prefer-team` / `--avoid-team` flags. The full list comes from
the Sleeper response; we don't hardcode it.
