"""Pipeline orchestration: ingest → parse → dbt → study → publish → commit.

Plain function calls, no orchestrator (see ``docs/SCALING.md`` for why).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def run_day(day: date, *, data_dir: Path, publish: bool = True) -> int:
    """Run the nightly pipeline for ``day``. Returns a process exit code."""
    log.info("run %s (data_dir=%s, publish=%s): stub", day, data_dir, publish)
    return 0


def backfill(start: date, end: date, *, data_dir: Path) -> int:
    """Backfill every trading day in ``[start, end]``. Returns a process exit code."""
    log.info("backfill %s..%s (data_dir=%s): stub", start, end, data_dir)
    return 0
