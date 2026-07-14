#!/usr/bin/env python3
"""Sample N evaluatable Sleeper leagues by crawling outward from a seed.

Sleeper has no league-browse endpoint, so discovery is a BFS over the
social graph: league -> its users -> each user's other leagues for the
season. Every league is qualified (complete NFL redraft season, no best
ball, no IDP slots — see ``common.qualifies``) and gets one team picked
at random among rosters that fielded a full lineup in the screen week
(a cheap dead-team filter; finer abandonment is flagged at eval time).

Deterministic given ``--rng-seed`` and a warm cache. All Sleeper
responses are disk-cached, so re-runs are offline.

Usage:
    uv run --project services/decision-engine python evals/discover_leagues.py \
        --seed-league 1182163805001936896 --season 2025 --count 100 \
        --rng-seed 42 --out evals/leagues_2025.json
"""

from __future__ import annotations

import argparse
import random
from collections import deque
from pathlib import Path
from typing import Any

from common import (
    DEFAULT_CACHE_DIR,
    CachedSleeperHttp,
    full_starter_lineup,
    qualifies,
    scoring_kind,
    write_json,
)
from decision_engine.clients.http import HttpError, NotFoundError

# Late-regular-season week used to screen for teams still setting
# lineups. Capped by the league's own playoff start when earlier.
SCREEN_WEEK = 14


def pick_team(
    http: CachedSleeperHttp,
    league_id: str,
    raw_league: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any] | None:
    """Choose one owned, active roster; None when no roster qualifies."""

    rosters = http.get_json(f"/v1/league/{league_id}/rosters")
    if not isinstance(rosters, list):
        return None

    playoff_start = (raw_league.get("settings") or {}).get("playoff_week_start") or 15
    screen_week = min(SCREEN_WEEK, max(int(playoff_start) - 1, 1))
    matchups = http.get_json(f"/v1/league/{league_id}/matchups/{screen_week}")
    starters_by_roster = {
        m.get("roster_id"): m.get("starters")
        for m in (matchups if isinstance(matchups, list) else [])
    }

    candidates = sorted(
        (
            r
            for r in rosters
            if isinstance(r, dict)
            and r.get("owner_id")
            and full_starter_lineup(starters_by_roster.get(r.get("roster_id")))
        ),
        key=lambda r: r["roster_id"],
    )
    if not candidates:
        return None
    return rng.choice(candidates)


def pinned_roster(
    http: CachedSleeperHttp, league_id: str, *, username: str
) -> dict[str, Any] | None:
    """The roster owned by ``username`` — used to pin the seed league's
    team to the same human the dashboard benchmarks."""

    try:
        user = http.get_json(f"/v1/user/{username}")
        rosters = http.get_json(f"/v1/league/{league_id}/rosters")
    except (NotFoundError, HttpError):
        return None
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if not user_id or not isinstance(rosters, list):
        return None
    return next(
        (
            r
            for r in rosters
            if isinstance(r, dict) and r.get("owner_id") == user_id
        ),
        None,
    )


def league_users(http: CachedSleeperHttp, league_id: str) -> list[dict[str, Any]]:
    try:
        users = http.get_json(f"/v1/league/{league_id}/users")
    except (NotFoundError, HttpError):
        return []
    return [u for u in users if isinstance(u, dict)] if isinstance(users, list) else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-league", required=True)
    parser.add_argument(
        "--seed-user",
        default=None,
        help="Sleeper username whose roster to pin for the seed league "
        "(so the eval benchmarks the same human as the dashboard)",
    )
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--rng-seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--max-visited",
        type=int,
        default=5000,
        help="hard stop on leagues examined, in case qualification is rare",
    )
    args = parser.parse_args()

    http = CachedSleeperHttp(args.cache_dir)
    rng = random.Random(args.rng_seed)

    queue: deque[tuple[str, int]] = deque([(args.seed_league, 0)])
    visited: set[str] = set()
    # Qualified leagues whose members haven't been used to widen the
    # frontier yet. Expansion is lazy — users/leagues-of-user calls are
    # the expensive part of the crawl, so they only happen when the
    # queue runs dry.
    expandable: deque[tuple[str, int]] = deque()
    results: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}

    while len(results) < args.count and len(visited) < args.max_visited:
        if not queue:
            if not expandable:
                print("frontier exhausted — cannot reach the requested count")
                break
            from_id, from_depth = expandable.popleft()
            fresh: list[str] = []
            for user in league_users(http, from_id):
                user_id = user.get("user_id")
                if not user_id:
                    continue
                try:
                    their = http.get_json(f"/v1/user/{user_id}/leagues/nfl/{args.season}")
                except (NotFoundError, HttpError):
                    continue
                for lg in their if isinstance(their, list) else []:
                    lid = lg.get("league_id") if isinstance(lg, dict) else None
                    if lid and lid not in visited:
                        fresh.append(lid)
            fresh = sorted(set(fresh))
            rng.shuffle(fresh)
            queue.extend((lid, from_depth + 1) for lid in fresh)
            continue

        league_id, depth = queue.popleft()
        if league_id in visited:
            continue
        visited.add(league_id)

        try:
            raw = http.get_json(f"/v1/league/{league_id}")
        except (NotFoundError, HttpError):
            rejections["fetch failed"] = rejections.get("fetch failed", 0) + 1
            continue

        ok, reason = qualifies(raw, season=args.season)
        if not ok:
            key = reason.split(" [")[0]
            rejections[key] = rejections.get(key, 0) + 1
            continue
        assert isinstance(raw, dict)

        is_seed = league_id == args.seed_league
        if is_seed and args.seed_user:
            team = pinned_roster(http, league_id, username=args.seed_user)
        else:
            team = pick_team(
                http, league_id, raw, random.Random(f"{args.rng_seed}:{league_id}")
            )
        if team is None:
            rejections["no active roster"] = rejections.get("no active roster", 0) + 1
            continue

        display_name = next(
            (
                u.get("display_name")
                for u in league_users(http, league_id)
                if u.get("user_id") == team["owner_id"]
            ),
            None,
        )

        results.append(
            {
                "league_id": league_id,
                "name": raw.get("name"),
                "num_teams": (raw.get("settings") or {}).get("num_teams"),
                "roster_id": team["roster_id"],
                "owner_user_id": team["owner_id"],
                "owner_display_name": display_name,
                "roster_positions": raw.get("roster_positions"),
                "scoring_kind": scoring_kind(raw),
                "playoff_week_start": (raw.get("settings") or {}).get(
                    "playoff_week_start"
                ),
                "crawl_depth": depth,
                "is_seed": is_seed,
            }
        )
        expandable.append((league_id, depth))
        print(
            f"[{len(results)}/{args.count}] {league_id} "
            f"depth={depth} {raw.get('name')!r} roster={team['roster_id']}"
            + (" (seed)" if is_seed else "")
        )

    write_json(args.out, results)
    print(f"\nwrote {len(results)} leagues -> {args.out}")
    print(f"visited={len(visited)} live_calls={http.live_calls} cache_hits={http.cache_hits}")
    if rejections:
        print("rejections:")
        for reason, n in sorted(rejections.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {reason}")
    http.close()


if __name__ == "__main__":
    main()
