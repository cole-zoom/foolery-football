#!/usr/bin/env python3
"""Aggregate eval results: do the models lose to typical humans, or
just to footballguys?

Reads ``evals/results/<season>/*.json`` and reports, per model:
win rate vs the human across leagues, weekly win rate, margins, and
lineup-efficiency distributions (actual / perfect). The seed league's
human is placed as a percentile within the human distribution — the
direct answer to "is footballguys an anomaly?".

Every per-league comparison uses only weeks where the human, the
perfect total, and *all* requested models have clean numbers, so every
series is summed over the identical week set.

Usage:
    uv run --project services/decision-engine python evals/aggregate.py \
        --season 2025 [--models naive,context,gbt] [--min-full-weeks 15]
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Any

from common import read_json, write_json

DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_REPORTS_DIR = Path(__file__).parent / "reports"


def league_summary(record: dict[str, Any], models: list[str]) -> dict[str, Any] | None:
    """Season sums over the weeks every series has clean data for."""

    weeks = []
    full_lineup_weeks = 0
    for week_str, rec in sorted(record["weeks"].items(), key=lambda kv: int(kv[0])):
        if "error" in rec:
            continue
        model_cells = rec.get("models", {})
        if any(m not in model_cells or "error" in model_cells[m] for m in models):
            continue
        if rec.get("human_actual") is None or rec.get("perfect_actual") is None:
            continue
        weeks.append((int(week_str), rec))
        if rec.get("human_full_lineup"):
            full_lineup_weeks += 1

    if not weeks:
        return None

    human = sum(rec["human_actual"] for _, rec in weeks)
    perfect = sum(rec["perfect_actual"] for _, rec in weeks)
    out: dict[str, Any] = {
        "league_id": record["league_id"],
        "name": record["name"],
        "owner": record.get("owner_display_name"),
        "is_seed": record.get("is_seed", False),
        "n_weeks": len(weeks),
        "full_lineup_weeks": full_lineup_weeks,
        "human_total": round(human, 1),
        "perfect_total": round(perfect, 1),
        "human_efficiency": round(human / perfect, 4) if perfect else None,
        "models": {},
    }
    for model in models:
        total = sum(rec["models"][model]["actual"] for _, rec in weeks)
        weekly_wins = sum(
            1
            for _, rec in weeks
            if rec["models"][model]["actual"] > rec["human_actual"]
        )
        out["models"][model] = {
            "total": round(total, 1),
            "margin_vs_human": round(total - human, 1),
            "beats_human": total > human,
            "weekly_win_rate": round(weekly_wins / len(weeks), 3),
            "efficiency": round(total / perfect, 4) if perfect else None,
        }
    return out


def percentile_of(value: float, population: list[float]) -> float:
    """Fraction of the population strictly below ``value``."""

    if not population:
        return 0.0
    return sum(1 for v in population if v < value) / len(population)


def dist(values: list[float]) -> dict[str, float]:
    qs = statistics.quantiles(values, n=4) if len(values) >= 4 else [min(values)] * 3
    return {
        "mean": round(statistics.fmean(values), 4),
        "min": round(min(values), 4),
        "p25": round(qs[0], 4),
        "median": round(statistics.median(values), 4),
        "p75": round(qs[2], 4),
        "max": round(max(values), 4),
    }


def report(summaries: list[dict[str, Any]], models: list[str], label: str) -> dict[str, Any]:
    n = len(summaries)
    human_eff = [s["human_efficiency"] for s in summaries]
    seed = next((s for s in summaries if s["is_seed"]), None)

    print(f"\n=== {label} ({n} leagues) ===")
    out: dict[str, Any] = {"label": label, "n_leagues": n, "models": {}}

    header = f"{'model':<10} {'beats human':>12} {'wkly win%':>10} {'avg margin':>11} {'med margin':>11} {'efficiency':>11}"
    print(header)
    print("-" * len(header))
    for model in models:
        wins = sum(1 for s in summaries if s["models"][model]["beats_human"])
        margins = [s["models"][model]["margin_vs_human"] for s in summaries]
        weekly = [s["models"][model]["weekly_win_rate"] for s in summaries]
        effs = [s["models"][model]["efficiency"] for s in summaries]
        out["models"][model] = {
            "beats_human_leagues": wins,
            "beats_human_rate": round(wins / n, 3),
            "weekly_win_rate_mean": round(statistics.fmean(weekly), 3),
            "margin": dist(margins),
            "efficiency": dist(effs),
        }
        print(
            f"{model:<10} {wins:>7}/{n:<4} "
            f"{statistics.fmean(weekly):>9.1%} "
            f"{statistics.fmean(margins):>+11.1f} "
            f"{statistics.median(margins):>+11.1f} "
            f"{statistics.fmean(effs):>11.1%}"
        )

    out["human_efficiency"] = dist(human_eff)
    print(
        f"{'human':<10} {'':>12} {'':>10} {'':>11} {'':>11} "
        f"{statistics.fmean(human_eff):>11.1%}"
    )
    print(
        f"\nhuman lineup efficiency: min {min(human_eff):.1%}  "
        f"p25 {out['human_efficiency']['p25']:.1%}  "
        f"median {statistics.median(human_eff):.1%}  "
        f"p75 {out['human_efficiency']['p75']:.1%}  max {max(human_eff):.1%}"
    )

    if seed is not None:
        pct = percentile_of(seed["human_efficiency"], human_eff)
        out["seed"] = {
            "league_id": seed["league_id"],
            "owner": seed["owner"],
            "human_efficiency": seed["human_efficiency"],
            "human_efficiency_percentile": round(pct, 3),
            "models": seed["models"],
        }
        print(
            f"\nseed league human ({seed['owner']!r}, {seed['name']!r}): "
            f"efficiency {seed['human_efficiency']:.1%} — "
            f"p{pct * 100:.0f} of all humans"
        )
        for model in models:
            m = seed["models"][model]
            print(
                f"  vs {model:<8} margin {m['margin_vs_human']:+.1f} "
                f"({'model wins' if m['beats_human'] else 'human wins'})"
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--models", default="naive,context,gbt")
    parser.add_argument(
        "--min-full-weeks",
        type=int,
        default=15,
        help="'engaged humans' view: leagues whose manager fielded a full "
        "lineup in at least this many evaluated weeks",
    )
    parser.add_argument("--min-weeks", type=int, default=14,
                        help="drop leagues with fewer clean evaluated weeks")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results_dir = args.results_dir / str(args.season)
    records = [read_json(p) for p in sorted(results_dir.glob("*.json"))]
    if not records:
        raise SystemExit(f"no results under {results_dir} — run run_eval.py first")

    summaries = []
    thin = 0
    for record in records:
        s = league_summary(record, models)
        if s is None or s["n_weeks"] < args.min_weeks:
            thin += 1
            continue
        summaries.append(s)
    if thin:
        print(f"excluded {thin} leagues with < {args.min_weeks} clean weeks")
    if not summaries:
        raise SystemExit("no leagues survived the week threshold")

    full = report(summaries, models, "all sampled leagues")
    engaged = [s for s in summaries if s["full_lineup_weeks"] >= args.min_full_weeks]
    engaged_out = None
    if engaged and len(engaged) < len(summaries):
        engaged_out = report(
            engaged,
            models,
            f"engaged humans (full lineup >= {args.min_full_weeks} wks; "
            f"{len(summaries) - len(engaged)} excluded)",
        )

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.reports_dir / f"summary_{args.season}.json",
        {"season": args.season, "all": full, "engaged": engaged_out, "leagues": summaries},
    )
    csv_path = args.reports_dir / f"leagues_{args.season}.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["league_id", "name", "owner", "is_seed", "n_weeks", "full_lineup_weeks",
             "human_total", "perfect_total", "human_efficiency", "model",
             "model_total", "margin_vs_human", "beats_human", "weekly_win_rate",
             "model_efficiency"]
        )
        for s in summaries:
            for model in models:
                m = s["models"][model]
                writer.writerow(
                    [s["league_id"], s["name"], s["owner"], s["is_seed"], s["n_weeks"],
                     s["full_lineup_weeks"], s["human_total"], s["perfect_total"],
                     s["human_efficiency"], model, m["total"], m["margin_vs_human"],
                     m["beats_human"], m["weekly_win_rate"], m["efficiency"]]
                )
    print(f"\nwrote {args.reports_dir / f'summary_{args.season}.json'} and {csv_path}")


if __name__ == "__main__":
    main()
