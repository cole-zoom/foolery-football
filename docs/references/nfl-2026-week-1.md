Last updated: 2026-06-12

# 2026 NFL schedule — week 1 (test fixture)

Used as the canonical example when writing tests and worked
examples in docs. Verify against Sleeper's `/v1/state/nfl` before
treating this as the "current week" — schedules can shift, and the
source for this table (a copy-pasted listing) didn't disambiguate
the two New York teams.

## Wednesday, Sep 9 2026

| Away | Home | Time (ET) | Venue | Line | O/U |
| -- | -- | -- | -- | -- | -- |
| New England | Seattle | 8:20 PM | Lumen Field, Seattle WA | SEA -3.5 | 44.5 |

## Thursday, Sep 10 2026

| Away | Home | Time (ET) | Venue | Line | O/U |
| -- | -- | -- | -- | -- | -- |
| San Francisco | LA Rams | 8:35 PM | Melbourne Cricket Ground, AU | LAR -3 | 48.5 |

## Sunday, Sep 13 2026

| Away | Home | Time (ET) | Venue | Line | O/U |
| -- | -- | -- | -- | -- | -- |
| Tampa Bay | Cincinnati | 1:00 PM | Paycor Stadium | CIN -3.5 | 50.5 |
| New Orleans | Detroit | 1:00 PM | Ford Field | DET -7 | 48.5 |
| NY Jets (assumed) | Tennessee | 1:00 PM | Nissan Stadium | TEN -3 | 39.5 |
| Baltimore | Indianapolis | 1:00 PM | Lucas Oil Stadium | BAL -3.5 | 49.5 |
| Atlanta | Pittsburgh | 1:00 PM | Acrisure Stadium | PIT -3 | 42.5 |
| Chicago | Carolina | 1:00 PM | Bank of America Stadium | CHI -2.5 | 44.5 |
| Cleveland | Jacksonville | 1:00 PM | EverBank Stadium | JAX -7.5 | 40.5 |
| Buffalo | Houston | 1:00 PM | NRG Stadium | BUF -1.5 | 45.5 |
| Miami | Las Vegas | 4:25 PM | Allegiant Stadium | LV -3 | 41.5 |
| Green Bay | Minnesota | 4:25 PM | U.S. Bank Stadium | GB -1.5 | 44.5 |
| Washington | Philadelphia | 4:25 PM | Lincoln Financial Field | PHI -5.5 | 46.5 |
| Arizona | LA Chargers | 4:25 PM | SoFi Stadium | LAC -11.5 | 45.5 |
| Dallas | NY Giants (assumed) | 8:20 PM | MetLife Stadium | DAL -2.5 | 48.5 |

## Monday, Sep 14 2026

| Away | Home | Time (ET) | Venue | Line | O/U |
| -- | -- | -- | -- | -- | -- |
| Denver | Kansas City | 8:15 PM | Arrowhead Stadium | KC -2.5 | 42.5 |

## Caveats

- The original source listed two "New York" matchups without
  disambiguating. The NFL has two New York teams: the **Jets**
  (`NYJ`, AFC) and the **Giants** (`NYG`, NFC). The 1 PM "New York
  @ Tennessee" is *assumed* to be the Jets, and the 8:20 PM
  "Dallas @ New York" is *assumed* to be the Giants. **Verify
  against an authoritative schedule** (NFL.com, Sleeper's
  `/v1/state/nfl` or matchup data) before relying on this as a
  real test fixture.
- Lines and totals are as of 2026-06-12 and will drift before
  kickoff.
- "Line: SEA -3.5" means Seattle is favored by 3.5 points.
- "O/U" is the over/under on total points scored in the game.
