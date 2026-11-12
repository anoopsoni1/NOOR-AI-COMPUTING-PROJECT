"""Retention: delete stale screenshots from disk and old tasks from MongoDB."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from backend.database.repository import Repository, run_db

log = logging.getLogger("nova.retention")


def purge_screenshots(root: Path, max_age_hours: int) -> int:
    if not root.exists():
        return 0
    cutoff, removed = time.time() - max_age_hours * 3600, 0
    for f in root.rglob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    for d in root.iterdir():  # remove emptied per-task folders
        if d.is_dir() and not any(d.iterdir()):
            shutil.rmtree(d, ignore_errors=True)
    return removed


async def run_retention(root: Path, repo: Repository, prefs: dict) -> None:
    shots = await run_db(purge_screenshots, root, prefs["screenshot_retention_hours"])
    tasks = await run_db(repo.purge_old_tasks, prefs["task_retention_days"])
    if shots or tasks:
        log.info("retention removed %d screenshots and %d tasks", shots, tasks)
