"""Offline tests for the aggregate step's attribution metrics (PRD 3.4).

Runs against the synthetic fixture under ``evals/testdata/`` — no
network, no snapshot. Invoke from the repo root:

    uv run --project services/decision-engine python -m pytest evals/test_aggregate.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aggregate import attribution, league_summary
from common import read_json

FIXTURE = Path(__file__).parent / "testdata" / "synthetic_results" / "2025" / "L1.json"
MODELS = ["oldmodel", "newmodel"]


@pytest.fixture()
def record() -> dict:
    return read_json(FIXTURE)


def test_league_summary_backward_compat_without_picks(record: dict) -> None:
    """Cells missing ``picks`` (pre-3.4 results) still aggregate."""

    s = league_summary(record, MODELS)
    assert s is not None
    assert s["n_weeks"] == 2
    assert s["human_total"] == pytest.approx(54.0)
    assert s["perfect_total"] == pytest.approx(75.0)

    old = s["models"]["oldmodel"]
    assert old["total"] == pytest.approx(45.0)
    assert old["margin_vs_human"] == pytest.approx(-9.0)
    assert old["weekly_win_rate"] == pytest.approx(0.5)
    # (30 + 28) predicted - (20 + 25) actual = 13 over 2 weeks.
    assert old["bias_per_week"] == pytest.approx(6.5)


def test_attribution_skips_models_without_picks(record: dict) -> None:
    attr = attribution([record], MODELS)
    assert attr["oldmodel"] == {"n_weeks": 0}


def test_attribution_ghost_start(record: dict) -> None:
    """Week 1 WR1: model started 'ghost' (0.0 actual) while an eligible
    bench alternative scored 8.0 — one ghost start, 8 points lost."""

    attr = attribution([record], MODELS)["newmodel"]
    assert attr["n_weeks"] == 2
    assert attr["ghost_starts"] == 1
    assert attr["ghost_starts_per_week"] == pytest.approx(0.5)
    assert attr["ghost_points_lost"] == pytest.approx(8.0)
    assert attr["ghost_points_per_week"] == pytest.approx(4.0)


def test_attribution_benched_best(record: dict) -> None:
    """Week 1: the human's best starter (h_rb, 20.0) is nowhere in the
    model lineup; the model's RB1 pick scored 12.0 -> 8 points lost.
    Week 2 the model itself started h_rb, so no benched-best there."""

    attr = attribution([record], MODELS)["newmodel"]
    assert attr["benched_best_weeks"] == 1
    assert attr["benched_best_rate"] == pytest.approx(0.5)
    assert attr["benched_best_points_lost"] == pytest.approx(8.0)


def test_attribution_loss_decomposition_sums_to_margin(record: dict) -> None:
    """Week 1 is the only losing week (12 vs 30, margin 18): the ghost
    slot accounts for 10 (human's 10 - model's 0), ranking error for 8
    (human's 20 - model's 12). Week 2 was a win — not decomposed."""

    attr = attribution([record], MODELS)["newmodel"]
    assert attr["losing_weeks"] == 1
    assert attr["loss_ghost_points"] == pytest.approx(10.0)
    assert attr["loss_ranking_points"] == pytest.approx(8.0)
    assert attr["loss_ghost_points"] + attr["loss_ranking_points"] == pytest.approx(
        30.0 - 12.0
    )
