"""Parquet lake on the ``data`` branch: immutable daily partitions plus small state tables.

Layout::

    {root}/bronze/{table}/date=YYYY-MM-DD/part-N.parquet   immutable, append-only
    {root}/state/{name}.parquet                             small mutable state

A partition file is never rewritten. A later run that finds *new* rows for a past
date (for example a report published after the nightly cut-off) writes the next
``part-N``. A run that finds nothing new writes nothing, which makes re-runs idempotent.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

# Fixed writer settings so equal frames produce equal bytes.
_PARQUET_KW: dict[str, object] = {
    "compression": "zstd",
    "compression_level": 9,
    "statistics": True,
    "use_pyarrow": False,
}


def partition_dir(root: Path, layer: str, table: str, day: date) -> Path:
    """Directory of one daily partition."""
    return root / layer / table / f"date={day.isoformat()}"


def partition_parts(root: Path, layer: str, table: str, day: date) -> list[Path]:
    """Existing part files of a partition, in write order."""
    d = partition_dir(root, layer, table, day)
    if not d.exists():
        return []
    return sorted(d.glob("part-*.parquet"), key=lambda p: int(p.stem.split("-")[1]))


def read_partition(root: Path, layer: str, table: str, day: date) -> pl.DataFrame | None:
    """Concatenate every part of one partition, or ``None`` if it doesn't exist."""
    parts = partition_parts(root, layer, table, day)
    if not parts:
        return None
    return pl.concat([pl.read_parquet(p) for p in parts], how="diagonal_relaxed")


def read_table(root: Path, layer: str, table: str) -> pl.DataFrame | None:
    """Read a whole table (all partitions), or ``None`` if empty."""
    base = root / layer / table
    files = sorted(base.glob("date=*/part-*.parquet")) + sorted(base.glob("month=*/*.parquet"))
    if not files:
        return None
    return pl.concat([pl.read_parquet(p) for p in files], how="diagonal_relaxed")


def write_frame(df: pl.DataFrame, path: Path) -> None:
    """Write ``df`` with the lake's fixed Parquet settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, **_PARQUET_KW)  # type: ignore[arg-type]


def append_partition(
    root: Path, layer: str, table: str, day: date, df: pl.DataFrame, *, key: str | None = None
) -> Path | None:
    """Append ``df`` as the next immutable part of a partition.

    With ``key``, rows whose key already exists in the partition are dropped first, so
    the partition never holds duplicates. Returns the new file, or ``None`` when there was
    nothing new to write.
    """
    existing = read_partition(root, layer, table, day)
    if key is not None and existing is not None and key in existing.columns:
        df = df.filter(~pl.col(key).is_in(existing[key].implode()))
    elif key is None and existing is not None:
        log.info("%s/%s %s already written; skipping", layer, table, day)
        return None
    if df.height == 0:
        return None
    if key is not None:
        df = df.sort(key)
    n = len(partition_parts(root, layer, table, day))
    path = partition_dir(root, layer, table, day) / f"part-{n}.parquet"
    write_frame(df, path)
    log.info("wrote %s (%d rows)", path, df.height)
    return path


def state_path(root: Path, name: str) -> Path:
    """Path of a small mutable state table."""
    return root / "state" / f"{name}.parquet"


def read_state(root: Path, name: str) -> pl.DataFrame | None:
    """Read a state table if present."""
    p = state_path(root, name)
    return pl.read_parquet(p) if p.exists() else None


def write_state(root: Path, name: str, df: pl.DataFrame) -> None:
    """Replace a state table."""
    write_frame(df, state_path(root, name))


def lake_size_bytes(root: Path) -> int:
    """Total size of all Parquet files under ``root`` (for the CI size check)."""
    return sum(p.stat().st_size for p in root.rglob("*.parquet"))
