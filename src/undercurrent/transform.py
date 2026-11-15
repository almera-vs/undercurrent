"""Silver/gold build: Python parsing + entity resolution, then ``dbt build``.

Outputs that are rebuilt from bronze every run (silver tables, the DuckDB file) live in
the work dir and are never committed. dbt runs in-process via ``dbtRunner``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from undercurrent import esef, gpw_calendar, parse, resolve

log = logging.getLogger(__name__)

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"


class DbtError(RuntimeError):
    """``dbt build`` failed (a model errored or a data test failed)."""


def duckdb_path(work: Path) -> Path:
    """Location of the DuckDB database dbt materialises into."""
    return work / "undercurrent.duckdb"


def run_dbt(
    data_dir: Path, work: Path, *, as_of: str | None = None, command: str = "build"
) -> None:
    """Run ``dbt <command>`` against the lake. Raises :class:`DbtError` on failure."""
    from dbt.cli.main import dbtRunner

    work.mkdir(parents=True, exist_ok=True)
    dbt_vars: dict[str, str] = {
        "data_dir": data_dir.resolve().as_posix(),
        "work_dir": work.resolve().as_posix(),
    }
    if as_of:
        dbt_vars["as_of"] = as_of
    os.environ["UNDERCURRENT_DUCKDB"] = duckdb_path(work).resolve().as_posix()
    args = [
        command,
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--target-path",
        str((work / "dbt_target").resolve()),
        "--log-path",
        str((work / "dbt_logs").resolve()),
        "--vars",
        json.dumps(dbt_vars),
        "--quiet",
    ]
    if command in ("build", "seed", "run"):
        # Everything is rebuilt from bronze each run; full refresh also picks up seed
        # schema changes that an incremental seed load would ignore.
        args.append("--full-refresh")
    res = dbtRunner().invoke(args)
    if not res.success:
        raise DbtError(f"dbt {command} failed: {res.exception or 'see dbt logs'}")
    log.info("dbt %s ok (as_of=%s)", command, as_of or "none")


CALENDAR_START = date(2010, 1, 1)


def write_calendar(work: Path, until: date | None = None) -> Path:
    """Every GPW session day (``gpw_calendar``) from 2010 to two years ahead, for dbt.

    ``sessions.sql`` reads it to tell a closed day from a day the lake is missing.
    """
    until = until or date(date.today().year + 2, 12, 31)
    days = [CALENDAR_START + timedelta(days=i) for i in range((until - CALENDAR_START).days + 1)]
    out = work / "silver" / "gpw_sessions.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"trade_date": [d for d in days if gpw_calendar.is_session(d)]},
        schema={"trade_date": pl.Date},
    ).write_parquet(out)
    return out


def build(data_dir: Path, work: Path, *, as_of: str | None = None, dbt: bool = True) -> None:
    """Rebuild silver from bronze, then run dbt (unless ``dbt=False``)."""
    parse.build_silver_reports(data_dir, work)
    resolve.resolve_reports(data_dir, work)
    esef.write_silver(data_dir, work)
    write_calendar(work)
    if dbt:
        run_dbt(data_dir, work, as_of=as_of)
