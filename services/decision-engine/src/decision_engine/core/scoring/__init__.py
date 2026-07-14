"""Scoring model registry.

The naive baseline lives in ``naive.py``. A smarter model lands as a
sibling module exposing a ``build(snapshot) -> ScoreFn`` factory —
register it in ``MODELS`` and the CLI selects via ``--model <name>``.
No edits required to ``core/pipeline.py`` or the entrypoint.

``build_score_fn`` is the cached front door the pipeline uses: a
factory's precompute runs once per (model, trimmed snapshot) and the
resulting ``ScoreFn`` is reused across requests. That is what keeps a
preference change (risk slider, team bias, week picker) from paying
the factory cost again — the expensive work never moves when a knob
does. Factories must therefore close over *derived* tables, not the
whole ``SnapshotData``, so a cached entry stays small after the season
cache evicts the snapshot it was built from (see ``protocol.py``).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Final

from decision_engine.core.scoring import context, gbt, naive
from decision_engine.core.scoring.protocol import ScoreFn, ScoreModelFactory
from decision_engine.types import SnapshotData

MODELS: Final[dict[str, ScoreModelFactory]] = {
    "naive": naive.build,
    "context": context.build,
    "gbt": gbt.build,
}

# (model, snapshot_dir, season, snapshot_version, weeks_included).
# weeks_included is in the key because the pipeline trims the snapshot
# per requested week — each replay week is its own build.
_BuildKey = tuple[str, str, int, str, tuple[int, ...]]
_BUILD_CACHE_MAX: Final[int] = 32
_build_cache: OrderedDict[_BuildKey, ScoreFn] = OrderedDict()
_build_lock = threading.Lock()


class UnknownModelError(ValueError):
    """``--model <name>`` doesn't match any registered model."""


def get_model(name: str) -> ScoreModelFactory:
    if name not in MODELS:
        raise UnknownModelError(
            f"unknown scoring model {name!r}; available: {sorted(MODELS)}"
        )
    return MODELS[name]


def build_score_fn(name: str, snapshot: SnapshotData) -> ScoreFn:
    """Build (or reuse) the ``ScoreFn`` for ``name`` over ``snapshot``.

    Snapshots without a version token (legacy manifests, hand-built test
    fixtures) can't be safely keyed, so they bypass the cache. A
    concurrent miss on the same key may build twice; the loser's result
    just replaces the winner's identical entry.
    """

    factory = get_model(name)
    if snapshot.snapshot_version is None:
        return factory(snapshot)

    key: _BuildKey = (
        name,
        snapshot.snapshot_dir,
        snapshot.season,
        snapshot.snapshot_version,
        snapshot.weeks_included,
    )
    with _build_lock:
        cached = _build_cache.get(key)
        if cached is not None:
            _build_cache.move_to_end(key)
            return cached

    score_fn = factory(snapshot)

    with _build_lock:
        _build_cache[key] = score_fn
        _build_cache.move_to_end(key)
        while len(_build_cache) > _BUILD_CACHE_MAX:
            _build_cache.popitem(last=False)
    return score_fn


__all__ = [
    "MODELS",
    "ScoreFn",
    "ScoreModelFactory",
    "UnknownModelError",
    "build_score_fn",
    "get_model",
]
