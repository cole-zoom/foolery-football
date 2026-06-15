"""Tests for config.resolve_settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from stats_loader.config import resolve_settings


def test_defaults_when_nothing_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STATS_LOADER_SNAPSHOT_ROOT", raising=False)
    monkeypatch.delenv("STATS_LOADER_SLEEPER_BASE_URL", raising=False)
    s = resolve_settings(
        snapshot_root=None,
        sleeper_base_url=None,
        dry_run=False,
        season_override=None,
        week_override=None,
    )
    # Default ends in data/seasons regardless of repo location.
    assert s.snapshot_root.parts[-2:] == ("data", "seasons")
    assert s.sleeper_base_url == "https://api.sleeper.app"
    assert s.dry_run is False
    assert s.state_override is None


def test_cli_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STATS_LOADER_SNAPSHOT_ROOT", str(tmp_path / "env"))
    cli_root = tmp_path / "cli"
    s = resolve_settings(
        snapshot_root=cli_root,
        sleeper_base_url="http://localhost:9000/",
        dry_run=True,
        season_override=None,
        week_override=None,
    )
    assert s.snapshot_root == cli_root
    assert s.sleeper_base_url == "http://localhost:9000"  # trailing slash stripped


def test_state_override_requires_both_flags() -> None:
    with pytest.raises(ValueError, match="together"):
        resolve_settings(
            snapshot_root=None,
            sleeper_base_url=None,
            dry_run=False,
            season_override=2025,
            week_override=None,
        )


def test_state_override_when_both_set() -> None:
    s = resolve_settings(
        snapshot_root=None,
        sleeper_base_url=None,
        dry_run=False,
        season_override=2025,
        week_override=5,
    )
    assert s.state_override is not None
    assert s.state_override.season == 2025
    assert s.state_override.week == 5
