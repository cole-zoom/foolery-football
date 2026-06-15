"""Build the manifest.json payload for a snapshot.

Pure. The shape comes from PRD 1.3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from stats_loader.core.state import FetchPlan
from stats_loader.types import Manifest

# Bumped any time we make a non-additive change to the snapshot format.
# The decision engine checks this on read and refuses snapshots whose
# schema_version is newer than the version it understands.
SCHEMA_VERSION: Final[int] = 1


def build_manifest(
    *,
    plan: FetchPlan,
    sources: dict[str, str],
    loader_version: str,
    started_at: datetime,
    finished_at: datetime,
) -> Manifest:
    return Manifest(
        schema_version=SCHEMA_VERSION,
        loader_version=loader_version,
        snapshot_started_at=started_at,
        snapshot_finished_at=finished_at,
        season=plan.season,
        completed_through_week=plan.completed_through_week,
        weeks_included=list(plan.completed_weeks),
        upcoming_week_projection=plan.upcoming_week,
        prior_season_bootstrapped=plan.bootstrap_prior_season,
        sources=dict(sources),
    )
