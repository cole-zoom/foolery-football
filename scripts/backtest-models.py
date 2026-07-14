#!/usr/bin/env python3
"""Replay a season week-by-week and compare scoring models.

For every week W, each model sees only weeks 1..W-1 (the pipeline's
``_snapshot_as_of`` replay contract) and predicts every fantasy-relevant
player's week-W points; predictions are scored against what actually
happened. Reports MAE per position and top-K precision (of the model's
top-K per position, how many landed in the actual top-K) — the metric
that matters for start/sit decisions.

Evaluation population per week: players at the covered positions who
played in week W and have at least one prior week of data. To keep
bench-warmers from flattering MAE, rows are weighted toward relevance
by also reporting "startable MAE" over players the model itself
projected at >= 8 points.

Usage:
    uv run --project services/decision-engine python scripts/backtest-models.py \
        [--season 2025] [--weeks 4-18] [--models naive,context,gbt]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from decision_engine.clients.snapshot_reader import FilesystemSnapshotReader
from decision_engine.core import pipeline
from decision_engine.core.scoring import build_score_fn
from decision_engine.core.scoring.common import weekly_points
from decision_engine.types import SnapshotData

# A vanilla PPR ruleset. The models are league-agnostic; this is just
# the yardstick the backtest measures in.
PPR_SCORING = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -1.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rec": 1.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "fum_lost": -2.0,
}

POSITIONS = ("QB", "RB", "WR", "TE")
TOP_K = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}
STARTABLE_THRESHOLD = 8.0


def evaluate(
    snapshot: SnapshotData, weeks: list[int], models: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """-> {model: {position: {mae, startable_mae, topk_precision, n}}}"""

    sums: dict[str, dict[str, dict[str, float]]] = {
        m: {p: {"ae": 0.0, "n": 0, "s_ae": 0.0, "s_n": 0, "hits": 0, "k": 0}
            for p in POSITIONS}
        for m in models
    }

    for week in weeks:
        actual_table = snapshot.weekly_stats.get(week)
        if not actual_table:
            continue
        trimmed = pipeline._snapshot_as_of(snapshot, week)
        if not trimmed.weekly_stats:
            continue

        candidates: dict[str, list[str]] = {p: [] for p in POSITIONS}
        for pid, stats in actual_table.items():
            player = snapshot.players.get(pid)
            if player is None or player.position not in POSITIONS:
                continue
            history = pipeline._build_history(pid, trimmed, None)
            if not history:
                continue
            candidates[player.position].append(pid)

        for model in models:
            score_fn = build_score_fn(model, trimmed)
            for pos, pids in candidates.items():
                preds: list[tuple[str, float, float]] = []
                for pid in pids:
                    history = pipeline._build_history(pid, trimmed, None)
                    score = score_fn(
                        snapshot.players[pid], history, PPR_SCORING, 0.5
                    )
                    actual = weekly_points(actual_table[pid], PPR_SCORING)
                    preds.append((pid, score.projected_mean, actual))

                bucket = sums[model][pos]
                for _pid, mu, actual in preds:
                    bucket["ae"] += abs(mu - actual)
                    bucket["n"] += 1
                    if mu >= STARTABLE_THRESHOLD:
                        bucket["s_ae"] += abs(mu - actual)
                        bucket["s_n"] += 1

                k = min(TOP_K[pos], len(preds))
                if k:
                    by_pred = {p for p, _, _ in sorted(preds, key=lambda t: -t[1])[:k]}
                    by_actual = {p for p, _, _ in sorted(preds, key=lambda t: -t[2])[:k]}
                    bucket["hits"] += len(by_pred & by_actual)
                    bucket["k"] += k

    out: dict[str, dict[str, dict[str, float]]] = {}
    for model, by_pos in sums.items():
        out[model] = {}
        for pos, b in by_pos.items():
            out[model][pos] = {
                "mae": b["ae"] / b["n"] if b["n"] else float("nan"),
                "startable_mae": b["s_ae"] / b["s_n"] if b["s_n"] else float("nan"),
                "topk_precision": b["hits"] / b["k"] if b["k"] else float("nan"),
                "n": b["n"],
            }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--weeks", default="4-18", help="inclusive range, e.g. 4-18")
    ap.add_argument("--models", default="naive,context,gbt")
    ap.add_argument("--root", default="data/seasons")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.weeks.split("-"))
    weeks = list(range(lo, hi + 1))
    models = args.models.split(",")

    reader = FilesystemSnapshotReader(Path(args.root), supported_schema_version=1)
    snapshot = reader.load(args.season)

    results = evaluate(snapshot, weeks, models)

    print(f"\nseason {args.season}, weeks {lo}-{hi}, PPR scoring\n")
    header = f"{'pos':<5}" + "".join(
        f"{m + ' MAE':>14}{m + ' sMAE':>14}{m + ' top-K':>14}" for m in models
    )
    print(header)
    for pos in POSITIONS:
        row = f"{pos:<5}"
        for m in models:
            r = results[m][pos]
            row += f"{r['mae']:>14.2f}{r['startable_mae']:>14.2f}{r['topk_precision']:>14.3f}"
        print(row)

    for m in models:
        total_ae = sum(
            results[m][p]["mae"] * results[m][p]["n"] for p in POSITIONS
        )
        total_n = sum(results[m][p]["n"] for p in POSITIONS)
        print(f"\n{m}: overall MAE {total_ae / total_n:.3f} over {total_n} player-weeks")


if __name__ == "__main__":
    main()
