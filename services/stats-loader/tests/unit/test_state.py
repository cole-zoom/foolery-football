"""Tests for core.state.plan_from_state."""

from __future__ import annotations

from stats_loader.core.state import plan_from_state
from stats_loader.types import NflState


def test_midseason_week_5_yields_weeks_1_through_4() -> None:
    plan = plan_from_state(NflState(season=2026, week=5))
    assert plan.completed_through_week == 4
    assert plan.completed_weeks == (1, 2, 3, 4)
    assert plan.upcoming_week == 5
    assert plan.bootstrap_prior_season is False


def test_week_1_bootstraps_prior_season() -> None:
    plan = plan_from_state(NflState(season=2026, week=1))
    assert plan.completed_through_week == 0
    assert plan.completed_weeks == ()
    assert plan.upcoming_week == 1
    assert plan.bootstrap_prior_season is True
    assert plan.prior_season == 2025


def test_week_0_means_no_upcoming_week_projection() -> None:
    plan = plan_from_state(NflState(season=2026, week=0))
    assert plan.completed_weeks == ()
    assert plan.upcoming_week is None
    assert plan.bootstrap_prior_season is True
