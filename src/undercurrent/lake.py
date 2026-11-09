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

# Column added to compacted monthly files so a single day can still be found.
PARTITION_COL = "_partition_date"

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
    """Existing part files of a *daily* partition, in write order."""
    d = partition_dir(root, layer, table, day)
    if not d.exists():
        return []
    return sorted(d.glob("part-*.parquet"), key=lambda p: int(p.stem.split("-")[1]))


def month_file(root: Path, layer: str, table: str, day: date) -> Path:
    """Compacted monthly file that would hold ``day``."""
    return root / layer / table / f"month={day:%Y-%m}" / "part-0.parquet"


def partition_exists(root: Path, layer: str, table: str, day: date) -> bool:
    """True if ``day`` has data, either as a daily partition or inside a compacted month."""
    if partition_parts(root, layer, table, day):
        return True
    mf = month_file(root, layer, table, day)
    if not mf.exists():
        return False
    return bool(
        pl.scan_parquet(mf).filter(pl.col(PARTITION_COL) == day).select(pl.len()).collect().item()
    )


def read_partition(root: Path, layer: str, table: str, day: date) -> pl.DataFrame | None:
    """All rows of ``day``: daily parts plus rows of that day in a compacted month."""
    frames = [pl.read_parquet(p) for p in partition_parts(root, layer, table, day)]
    mf = month_file(root, layer, table, day)
    if mf.exists():
        m = pl.read_parquet(mf).filter(pl.col(PARTITION_COL) == day).drop(PARTITION_COL)
        if m.height:
            frames.insert(0, m)
    if not frames:
        return None
    return pl.concat(frames, how="diagonal_relaxed")


def read_table(root: Path, layer: str, table: str) -> pl.DataFrame | None:
    """Read a whole table (all partitions), or ``None`` if empty."""
    base = root / layer / table
    files = sorted(base.glob("date=*/part-*.parquet")) + sorted(base.glob("month=*/*.parquet"))
    if not files:
        return None
    frames = [pl.read_parquet(p) for p in files]
    frames = [f.drop(PARTITION_COL) if PARTITION_COL in f.columns else f for f in frames]
    return pl.concat(frames, how="diagonal_relaxed")


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
    if key is None and existing is None and partition_exists(root, layer, table, day):
        return None
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


def compact_month(root: Path, layer: str, table: str, month: date) -> Path | None:
    """Merge all daily partitions of ``month`` into ``month=YYYY-MM/part-0.parquet``.

    Daily files are deleted afterwards. A month that is already compacted is left alone,
    so the workflow is idempotent. Returns the monthly file, or None if nothing to do.
    """
    base = root / layer / table
    dirs = sorted(base.glob(f"date={month:%Y-%m}-*"))
    out = month_file(root, layer, table, month)
    if not dirs or out.exists():
        return None
    frames = []
    for d in dirs:
        day = date.fromisoformat(d.name.removeprefix("date="))
        parts = sorted(d.glob("part-*.parquet"), key=lambda p: int(p.stem.split("-")[1]))
        for p in parts:
            frames.append(pl.read_parquet(p).with_columns(pl.lit(day).alias(PARTITION_COL)))
    df = pl.concat(frames, how="diagonal_relaxed")
    write_frame(df, out)
    check = pl.read_parquet(out)
    if check.height != df.height:
        out.unlink()
        raise RuntimeError(f"compaction of {base} {month:%Y-%m} lost rows")
    for d in dirs:
        for p in d.glob("*.parquet"):
            p.unlink()
        d.rmdir()
    log.info(
        "compacted %s %s: %d daily dirs -> %s (%d rows)",
        base,
        f"{month:%Y-%m}",
        len(dirs),
        out,
        df.height,
    )
    return out
