"""Scoring model registry.

The naive baseline lives in ``naive.py``. A smarter model lands as a
sibling module exposing a ``build(snapshot) -> ScoreFn`` factory —
register it in ``MODELS`` and the CLI selects via ``--model <name>``.
No edits required to ``core/pipeline.py`` or the entrypoint.
"""

from __future__ import annotations

from typing import Final

from decision_engine.core.scoring import naive
from decision_engine.core.scoring.protocol import ScoreFn, ScoreModelFactory

MODELS: Final[dict[str, ScoreModelFactory]] = {
    "naive": naive.build,
}


class UnknownModelError(ValueError):
    """``--model <name>`` doesn't match any registered model."""


def get_model(name: str) -> ScoreModelFactory:
    if name not in MODELS:
        raise UnknownModelError(
            f"unknown scoring model {name!r}; available: {sorted(MODELS)}"
        )
    return MODELS[name]


__all__ = ["MODELS", "ScoreFn", "ScoreModelFactory", "UnknownModelError", "get_model"]
