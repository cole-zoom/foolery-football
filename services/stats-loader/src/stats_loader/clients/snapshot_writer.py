"""Filesystem writer for per-season snapshot folders.

Snapshots are keyed by season: ``data/seasons/<year>/``. Each new run for
a season replaces the existing folder atomically:

1. Write all artifacts to ``<root>/.tmp-<season>-<pid>/``.
2. Write ``manifest.json`` LAST. It's the commit marker.
3. If a ``<root>/<season>/`` already exists, rename it to
   ``<root>/.bak-<season>-<pid>/``.
4. ``os.rename`` the temp folder into place.
5. Remove the ``.bak-`` folder.

A crashed mid-write run leaves a ``.tmp-...`` or ``.bak-...`` folder,
never a visible half-snapshot. ``cleanup_stale_tmp`` removes both on the
next run.

``os.rename`` is atomic within a filesystem on POSIX; we're local-only,
so that's what we care about.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

TMP_PREFIX = ".tmp-"
BAK_PREFIX = ".bak-"
MANIFEST_NAME = "manifest.json"


class SnapshotWriter(Protocol):
    """Protocol consumed by ``core.pipeline``."""

    def write_artifact(self, name: str, payload: object) -> None: ...
    def commit(self, manifest_payload: dict[str, object]) -> Path: ...


class AtomicSnapshotWriter:
    """Writes a single season's snapshot folder atomically.

    One instance == one snapshot. Don't reuse.
    """

    def __init__(self, root: Path, season: int) -> None:
        self._root = root
        self._season = season
        self._tmp_path: Path | None = None
        self._committed = False

    def write_artifact(self, name: str, payload: object) -> None:
        """Write a JSON artifact into the (lazily-created) temp folder."""

        if self._committed:
            raise RuntimeError("Snapshot already committed; writer is single-use.")
        if name == MANIFEST_NAME:
            raise ValueError("Use commit() to write the manifest, not write_artifact().")

        tmp = self._ensure_tmp()
        _write_json_file(tmp / name, payload)

    def commit(self, manifest_payload: dict[str, object]) -> Path:
        """Write the manifest, then swap temp into place. Returns final path."""

        if self._committed:
            raise RuntimeError("Snapshot already committed.")
        tmp = self._ensure_tmp()

        # Manifest is the commit marker; write it last.
        _write_json_file(tmp / MANIFEST_NAME, manifest_payload)

        final_path = self._root / str(self._season)
        bak_path: Path | None = None
        if final_path.exists():
            bak_path = self._root / f"{BAK_PREFIX}{self._season}-{os.getpid()}"
            if bak_path.exists():
                shutil.rmtree(bak_path)
            os.rename(final_path, bak_path)

        try:
            os.rename(tmp, final_path)
        except OSError:
            if bak_path is not None and bak_path.exists():
                os.rename(bak_path, final_path)
            raise

        if bak_path is not None:
            shutil.rmtree(bak_path, ignore_errors=True)

        self._committed = True
        self._tmp_path = None
        log.info("Snapshot committed: %s", final_path)
        return final_path

    def abort(self) -> None:
        """Remove the temp folder if anything was written. Safe to call repeatedly."""

        if self._tmp_path and self._tmp_path.exists():
            shutil.rmtree(self._tmp_path, ignore_errors=True)
            log.info("Aborted snapshot; removed %s", self._tmp_path)
        self._tmp_path = None

    @property
    def tmp_path(self) -> Path | None:
        """Visible for tests."""

        return self._tmp_path

    def _ensure_tmp(self) -> Path:
        if self._tmp_path is None:
            self._root.mkdir(parents=True, exist_ok=True)
            tmp = self._root / f"{TMP_PREFIX}{self._season}-{os.getpid()}"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir()
            self._tmp_path = tmp
        return self._tmp_path


def cleanup_stale_tmp(root: Path) -> int:
    """Remove leftover ``.tmp-`` and ``.bak-`` folders from crashed prior runs.

    Returns the count removed. Called at the start of every run.
    """

    if not root.exists():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and (
            child.name.startswith(TMP_PREFIX) or child.name.startswith(BAK_PREFIX)
        ):
            shutil.rmtree(child, ignore_errors=True)
            log.info("Cleaned up stale folder: %s", child)
            removed += 1
    return removed


def _write_json_file(path: Path, payload: object) -> None:
    # ``sort_keys`` keeps diffs across snapshots stable (PRD 1.3 motivation).
    # ``ensure_ascii=False`` keeps player names readable in the file.
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, ensure_ascii=False)
