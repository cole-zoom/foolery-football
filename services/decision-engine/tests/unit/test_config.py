"""Tests for config.settings.resolve_settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_engine.config.settings import resolve_settings


def _base_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        user="cole",
        league_id="L1",
        slot="flex",
        risk=0.3,
        pool="roster",
        limit=10,
        model="naive",
        prefer_team=None,
        avoid_team=None,
        season=None,
        week=None,
        snapshot_root=Path("/tmp/snap"),
        sleeper_base_url=None,
    )
    base.update(overrides)
    return base


def test_resolve_settings_uppercases_slot_and_teams() -> None:
    s = resolve_settings(**_base_kwargs(prefer_team="det", avoid_team="chi"))  # type: ignore[arg-type]
    assert s.slot == "FLEX"
    assert s.prefer_team == "DET"
    assert s.avoid_team == "CHI"


def test_resolve_settings_strips_whitespace() -> None:
    s = resolve_settings(**_base_kwargs(user="  cole  ", league_id="  L1  "))  # type: ignore[arg-type]
    assert s.user == "cole"
    assert s.league_id == "L1"


def test_resolve_settings_rejects_empty_user() -> None:
    with pytest.raises(ValueError, match="--user"):
        resolve_settings(**_base_kwargs(user="   "))  # type: ignore[arg-type]


def test_resolve_settings_rejects_out_of_range_risk() -> None:
    with pytest.raises(ValueError, match="--risk"):
        resolve_settings(**_base_kwargs(risk=1.5))  # type: ignore[arg-type]


def test_resolve_settings_rejects_zero_limit() -> None:
    with pytest.raises(ValueError, match="--limit"):
        resolve_settings(**_base_kwargs(limit=0))  # type: ignore[arg-type]
