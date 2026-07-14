#!/usr/bin/env python3
"""Replay every sampled league's season and compare models to the human.

For each (league, week, model) this runs the same leakage-safe replay
the web comparison view uses (``decision_engine.core.replay``): the
model re-picks the lineup from the week-W matchup-archive roster seeing
only weeks < W, the human total is what the manager actually fielded,
and the perfect total is the hindsight-optimal assignment.

Loop order is week-major (week -> league -> model) on purpose: model
factories are LRU-cached per (model, trimmed snapshot) with room for
~32 entries, and context/GBT fit lazily per unique scoring config
inside the factory. Week-major keeps one factory per model hot so each
unique scoring dict pays one GBT fit per week; league-major would cycle
54 cache keys and refit constantly.

Results checkpoint to ``evals/results/<season>/<league_id>.json`` after
every completed (league, week) — Ctrl-C safe, re-runs resume.

Usage:
    uv run --project services/decision-engine python evals/run_eval.py \
        --leagues evals/leagues_2025.json --season 2025 --weeks 1-18 \
        --models naive,context,gbt [--limit 5] [--force]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from common import (
    DEFAULT_CACHE_DIR,
    CachedSleeperHttp,
    full_starter_lineup,
    parse_weeks,
    read_json,
    write_json,
)
from decision_engine.clients.snapshot_reader import FilesystemSnapshotReader
from decision_engine.core.eligibility import player_eligible_for_slot
from decision_engine.core.league_fetch import (
    UserInputError,
    fetch_league_context_by_roster,
    fetch_matchups,
)
from decision_engine.core.replay import WeekComparison, replay_week_comparison
from decision_engine.core.scoring.common import weekly_points
from decision_engine.types import SnapshotData

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"


def load_results(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        return read_json(path)
    return {
        "league_id": entry["league_id"],
        "name": entry["name"],
        "roster_id": entry["roster_id"],
        "owner_display_name": entry.get("owner_display_name"),
        "is_seed": entry.get("is_seed", False),
        "weeks": {},
    }


def picks_payload(
    result: WeekComparison, snapshot: SnapshotData
) -> list[dict[str, Any]]:
    """Per-slot picks for the results file (PRD 3.4 attribution).

    ``best_alt_actual`` is the best actual score among week-W roster
    players eligible for the slot and *not* in the model's lineup — the
    points a ghost start left on the bench. Persisted here because the
    aggregate step has no roster/eligibility data.
    """

    actual_table = snapshot.weekly_stats.get(result.week, {})
    scoring = result.league_context.league.scoring_settings
    roster = result.league_context.user_roster.players
    model_lineup = {p.model_player_id for p in result.slot_picks if p.model_player_id}

    def actual_of(pid: str | None) -> float | None:
        if not pid or pid not in snapshot.players:
            return None
        stats = actual_table.get(pid)
        return round(weekly_points(stats, scoring), 2) if stats else None

    picks: list[dict[str, Any]] = []
    for p in result.slot_picks:
        best_alt = 0.0
        for pid in roster:
            if pid in model_lineup:
                continue
            player = snapshot.players.get(pid)
            if player is None or not player_eligible_for_slot(player, p.slot):
                continue
            alt = actual_of(pid)
            if alt is not None and alt > best_alt:
                best_alt = alt
        model_predicted = (
            result.predicted_mean.get(p.model_player_id)
            if p.model_player_id
            else None
        )
        picks.append(
            {
                "slot": p.slot_id,
                "model": p.model_player_id,
                "human": p.human_player_id,
                "model_predicted": (
                    round(model_predicted, 2) if model_predicted is not None else None
                ),
                "model_actual": actual_of(p.model_player_id),
                "human_actual": actual_of(p.human_player_id),
                "best_alt_actual": round(best_alt, 2),
            }
        )
    return picks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", type=Path, required=True)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--weeks", default="1-18")
    parser.add_argument("--models", default="naive,context,gbt")
    parser.add_argument("--limit", type=int, default=None, help="first N leagues only")
    parser.add_argument("--force", action="store_true", help="recompute existing cells")
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument("--pool", default="roster", choices=("roster", "waivers", "both"))
    parser.add_argument(
        "--availability",
        default="sleeper",
        choices=("sleeper", "heuristic", "none"),
        help="availability gate source (milestone 4 run B uses 'heuristic'; "
        "pair non-default modes with a separate --results-dir)",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--snapshot-root", type=Path, default=REPO_ROOT / "data" / "seasons"
    )
    args = parser.parse_args()

    weeks = parse_weeks(args.weeks)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    leagues = read_json(args.leagues)
    if args.limit:
        leagues = leagues[: args.limit]

    http = CachedSleeperHttp(args.cache_dir)
    reader = FilesystemSnapshotReader(args.snapshot_root, supported_schema_version=1)
    snapshot = reader.load(args.season)
    playable_weeks = [w for w in weeks if snapshot.weekly_stats.get(w)]
    skipped = sorted(set(weeks) - set(playable_weeks))
    if skipped:
        print(f"skipping weeks with no recorded stats: {skipped}")

    results_dir = args.results_dir / str(args.season)
    results: dict[str, dict[str, Any]] = {}
    contexts: dict[str, Any] = {}
    for entry in leagues:
        lid = entry["league_id"]
        results[lid] = load_results(results_dir / f"{lid}.json", entry)

    total_cells = len(playable_weeks) * len(leagues) * len(models)
    done_cells = 0
    started = time.monotonic()

    for week in playable_weeks:
        for entry in leagues:
            lid = entry["league_id"]
            record = results[lid]
            week_rec = record["weeks"].get(str(week), {})
            models_todo = [
                m
                for m in models
                if args.force or m not in week_rec.get("models", {})
            ]
            if not models_todo:
                done_cells += len(models)
                continue

            if lid not in contexts:
                try:
                    contexts[lid] = fetch_league_context_by_roster(
                        http, league_id=lid, roster_id=entry["roster_id"]
                    )
                except UserInputError as exc:
                    contexts[lid] = exc
            context = contexts[lid]
            if isinstance(context, Exception):
                week_rec["error"] = f"league context: {context}"
                record["weeks"][str(week)] = week_rec
                write_json(results_dir / f"{lid}.json", record)
                done_cells += len(models)
                continue

            matchups = fetch_matchups(http, league_id=lid, week=week)
            my_matchup = next(
                (m for m in matchups if m.roster_id == entry["roster_id"]), None
            )
            if my_matchup is None:
                week_rec["error"] = "no matchup this week"
                record["weeks"][str(week)] = week_rec
                write_json(results_dir / f"{lid}.json", record)
                done_cells += len(models)
                continue

            week_rec.setdefault("models", {})
            week_rec["human_full_lineup"] = full_starter_lineup(
                list(my_matchup.starters)
            )
            t0 = time.monotonic()
            for model in models_todo:
                try:
                    result = replay_week_comparison(
                        http=http,
                        snapshot_reader=reader,
                        snapshot=snapshot,
                        league_context=context,
                        matchups=matchups,
                        season=args.season,
                        week=week,
                        model=model,
                        risk=args.risk,
                        pool=args.pool,
                        availability=args.availability,
                    )
                except Exception as exc:  # quarantine, keep the run alive
                    week_rec["models"][model] = {"error": f"{type(exc).__name__}: {exc}"}
                    continue
                week_rec["models"][model] = {
                    "predicted": round(result.model_predicted, 2),
                    "actual": round(result.model_actual, 2),
                    "picks": picks_payload(result, snapshot),
                }
                # Model-independent columns; first model writes, others verify.
                for key, value in (
                    ("human_actual", round(result.human_actual, 2)),
                    (
                        "perfect_actual",
                        round(result.perfect_actual, 2)
                        if result.perfect_actual is not None
                        else None,
                    ),
                    ("using_prior_season", result.using_prior_season),
                ):
                    if key in week_rec and week_rec[key] != value:
                        raise AssertionError(
                            f"{lid} wk{week} {model}: {key} disagrees "
                            f"({week_rec[key]} != {value})"
                        )
                    week_rec[key] = value

            record["weeks"][str(week)] = week_rec
            write_json(results_dir / f"{lid}.json", record)

            done_cells += len(models)
            elapsed = time.monotonic() - started
            rate = done_cells / elapsed if elapsed > 0 else 0.0
            eta_min = (total_cells - done_cells) / rate / 60 if rate else float("inf")
            print(
                f"wk{week:>2} {lid} ({entry['name']!r:.30}) "
                f"{len(models_todo)} models in {time.monotonic() - t0:.1f}s "
                f"| {done_cells}/{total_cells} cells, eta {eta_min:.0f}m"
            )

    print(
        f"\ndone: {done_cells}/{total_cells} cells in "
        f"{(time.monotonic() - started) / 60:.1f}m "
        f"(live_calls={http.live_calls} cache_hits={http.cache_hits})"
    )
    http.close()


if __name__ == "__main__":
    main()
