#!/usr/bin/env bash
# Walk the Sleeper previous_league_id chain backwards to find every season
# a league has run. Useful for finding a test fixture that spans multiple
# years so you can exercise the season picker.
#
# Usage:
#   scripts/find-league-chain.sh <league_id>
#   scripts/find-league-chain.sh --user <username>           # uses first 2025 league
#   scripts/find-league-chain.sh --user <username> --season 2024
#
# Examples:
#   scripts/find-league-chain.sh 1182163805001936896
#   scripts/find-league-chain.sh --user ben
#   scripts/find-league-chain.sh --user footballguys --season 2025

set -euo pipefail

API="https://api.sleeper.app/v1"
MAX_DEPTH=15

usage() {
  sed -n '2,15p' "$0"
  exit 1
}

mode=""
arg=""
season=2025

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)   mode=user; arg="$2"; shift 2 ;;
    --season) season="$2"; shift 2 ;;
    -h|--help) usage ;;
    *)
      if [[ -z "$arg" ]]; then
        mode=league; arg="$1"; shift
      else
        usage
      fi
      ;;
  esac
done

[[ -z "$arg" ]] && usage

if [[ "$mode" == "user" ]]; then
  user_payload=$(curl -sf "$API/user/$arg") || { echo "user '$arg' not found" >&2; exit 1; }
  user_id=$(echo "$user_payload" | python3 -c "import json,sys; print(json.load(sys.stdin).get('user_id',''))")
  [[ -z "$user_id" ]] && { echo "user '$arg' not found" >&2; exit 1; }

  echo "user '$arg' → user_id=$user_id"
  leagues=$(curl -sf "$API/user/$user_id/leagues/nfl/$season")
  count=$(echo "$leagues" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
  echo "  $count league(s) for season $season"
  [[ "$count" -eq 0 ]] && exit 0

  echo "$leagues" | python3 -c "
import json, sys
for lg in json.load(sys.stdin):
    print(f\"    {lg['league_id']}  '{lg['name']}'\")
"
  league_id=$(echo "$leagues" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['league_id'])")
  echo ""
  echo "tracing chain from $league_id..."
else
  league_id="$arg"
fi

echo ""
printf "%-7s  %-22s  %s\n" "season" "league_id" "name"
printf "%-7s  %-22s  %s\n" "------" "----------------------" "----"

cur="$league_id"
for ((i=0; i<MAX_DEPTH; i++)); do
  [[ -z "$cur" || "$cur" == "0" || "$cur" == "null" ]] && break
  resp=$(curl -sf "$API/league/$cur") || { echo "request failed for $cur" >&2; exit 1; }
  echo "$resp" | python3 -c "
import json, sys
lg = json.load(sys.stdin)
print(f\"{lg['season']:<7}  {lg['league_id']:<22}  '{lg['name']}'\")
"
  cur=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('previous_league_id') or '')")
done
