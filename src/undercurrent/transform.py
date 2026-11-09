"""Silver/gold build: Python parsing + entity resolution, then ``dbt build``.

Outputs that are rebuilt from bronze every run (silver tables, the DuckDB file) live in
the work dir and are never committed. dbt runs in-process via ``dbtRunner``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from undercurrent import parse, resolve

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
    res = dbtRunner().invoke(args)
    if not res.success:
        raise DbtError(f"dbt {command} failed: {res.exception or 'see dbt logs'}")
    log.info("dbt %s ok (as_of=%s)", command, as_of or "none")


def build(data_dir: Path, work: Path, *, as_of: str | None = None, dbt: bool = True) -> None:
    """Rebuild silver from bronze, then run dbt (unless ``dbt=False``)."""
    parse.build_silver_reports(data_dir, work)
    resolve.resolve_reports(data_dir, work)
    if dbt:
        run_dbt(data_dir, work, as_of=as_of)
