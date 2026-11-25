"""Do reports move the price? The reaction to every kind of report, from the market state.

The event study (``study.py``) asks whether a reader can still profit by trading *after* the
evening digest, net of costs. That is a different question from whether a kind of report
moves the price: the move usually happens on the report's own session, before any reader of
the digest can buy, and it goes either way, so it averages out in a signed mean. This module
measures the move itself.

* **Every report**, of every kind the classifier gives (``study.signal_key`` for signal
  events, plus ``periodic`` for quarterly, half-year and annual reports and ``other``).
* **Reaction window:** from day 0 (the first session whose close is after ``known_at``) to
  the entry session (the first close a reader of the digest can trade, ``study.entry_offset``)
  inclusive: exactly the sessions the event study leaves out. At most ``MAX_WINDOW``
  sessions; a window across a hole in the lake is dropped.
* **Size of a move:** the market state's surprises (residual return ÷ its forecast σ) summed
  over the window and divided by √(sessions), so a calm day is about ±1 whatever the window's
  length; and the residual return itself, in percent, for the page.
* **Ordinary days:** windows of the same length on stocks of the same liquidity tiers with no
  report of any kind with day 0 within ``QUIET`` sessions. On them the mean squared surprise is
  close to 1 (the market state's σ is calibrated), and that is the yardstick.
* **Per kind:** ``ratio`` = mean of z² ÷ the ordinary mean for the same window length (1 = no
  bigger than an ordinary day); the share of windows beyond ±2 against the ordinary share;
  the median absolute residual move against the ordinary median. A stock with several
  reports of one kind on one day counts once. The test is a two-way clustered t (stock,
  calendar month) of z² ÷ ordinary − 1, and BH q-values across kinds. Verdict ``moves`` when
  q < 5 % and the ratio is at least ``MIN_RATIO``; ``no_clear_move`` otherwise; ``too_few``
  with fewer than ``MIN_EVENTS`` reports.
* **Point in time:** reports and state rows known after ``as_of`` are dropped; a window whose
  sessions are not all known yet counts only for ``report_reactions`` (the digest shows what
  is known by the evening), never for the statistics.
* Only ``liquid`` and ``tradable`` stocks on day 0: a thin stock's stale price makes its
  surprise meaningless (the market state's own rule).
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl

from undercurrent import lake, study

log = logging.getLogger(__name__)

STATS_FILE = "reaction_stats.parquet"
REPORTS_FILE = "report_reactions.parquet"
MAX_WINDOW = 3  # sessions from day 0 to entry, at most
QUIET = 2  # sessions on either side of an ordinary day without any report
MIN_EVENTS = 30
MIN_RATIO = 1.5  # "clearly": a report day's squared move at least 1.5 times an ordinary one
BIG = 2.0  # a window beyond ±2 (in its own σ) is a big move
SIGNIFICANCE = 0.05
TIERS = ("liquid", "tradable")
_CLOSE = time(17, 5)


@dataclass(frozen=True)
class Inputs:
    """Point-in-time inputs: every classified report, the session calendar, the state."""

    as_of: datetime
    reports: pl.DataFrame  # report_id, isin, signal_key, known_at
    sessions: list[date]
    state: pl.DataFrame  # isin, trade_date, surprise, resid, liquidity_tier


def kind_of(signal_type: str | None, subtype: str | None, side: str | None) -> str | None:
    """The kind a report is measured under: the study's key, ``periodic`` or ``other``."""
    if signal_type is None:
        return None
    if signal_type in ("periodic", "other"):
        return signal_type
    return study.signal_key(signal_type, subtype, side)


def load_inputs(duckdb_file: Path, work: Path, as_of: datetime) -> Inputs:
    """Read the reports and sessions from DuckDB and the market state from the work dir."""
    import duckdb

    con = duckdb.connect(str(duckdb_file))
    try:
        reps = con.sql(
            "select report_id, isin, signal_type, subtype, insider_side, known_at from reports "
            "where isin is not null and signal_type is not null"
        ).pl()
        sessions = sorted(r[0] for r in con.sql("select trade_date from sessions").fetchall())
    finally:
        con.close()
    state = pl.read_parquet(
        work / "gold" / "market_state.parquet",
        columns=["isin", "trade_date", "surprise", "resid", "liquidity_tier", "known_at"],
    )
    return cut(reps, sessions, state, as_of)


def cut(
    reports: pl.DataFrame, sessions: list[date], state: pl.DataFrame, as_of: datetime
) -> Inputs:
    """Keep what was known by ``as_of`` and give every report its kind."""
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))
    known = pl.col("known_at").cast(pl.Datetime("us", "UTC"))
    reps = reports.with_columns(known.alias("known_at")).filter(pl.col("known_at") <= ts)
    if "signal_key" not in reps.columns:
        reps = reps.with_columns(
            pl.struct("signal_type", "subtype", "insider_side")
            .map_elements(
                lambda s: kind_of(s["signal_type"], s["subtype"], s["insider_side"]),
                return_dtype=pl.String,
            )
            .alias("signal_key")
        )
    reps = reps.filter(pl.col("signal_key").is_not_null()).select(
        "report_id", "isin", "signal_key", "known_at"
    )
    st = state.filter(known <= ts).drop("known_at")
    last = as_of.astimezone(study.WARSAW).date()
    return Inputs(as_of, reps, [s for s in sessions if s <= last], st)


def windows(reports: pl.DataFrame, sessions: list[date]) -> pl.DataFrame:
    """Each report's day 0, entry offset and window sessions (one row per window session).

    Day 0 may be a session the calendar does not hold yet (a report after today's close):
    such a report gets no rows, and the digest shows nothing for it until it is known.
    """
    from undercurrent import gpw_calendar

    ext = _extend(sessions, 2 + study.MAX_ENTRY_LAG)
    first_session: dict[date, date] = {}

    def gpw_day0(d: date) -> date:
        if d not in first_session:
            s = d
            while not gpw_calendar.is_session(s):
                s += timedelta(days=1)
            first_session[d] = s
        return first_session[d]

    rows: dict[str, list[object]] = {k: [] for k in ("report_id", "day0", "k", "pos", "trade_date")}
    for rid, known_at in zip(reports["report_id"], reports["known_at"], strict=True):
        local = known_at.astimezone(study.WARSAW)
        d = local.date() if local.time() < _CLOSE else local.date() + timedelta(days=1)
        i0 = bisect.bisect_left(ext, d)
        # day 0 must be the exchange's first session from d: not one the lake lacks
        if i0 >= len(ext) or ext[i0] != gpw_day0(d):
            continue
        off = study.entry_offset(known_at, ext[i0], ext)
        if off < 0 or off + 1 > MAX_WINDOW:
            continue
        span = ext[i0 : i0 + off + 1]
        if any(study.is_hole(a, b) for a, b in pairwise(span)):
            continue
        for j, s in enumerate(span):
            rows["report_id"].append(rid)
            rows["day0"].append(ext[i0])
            rows["k"].append(off + 1)
            rows["pos"].append(j)
            rows["trade_date"].append(s)
    return pl.DataFrame(
        rows,
        schema={
            "report_id": pl.String,
            "day0": pl.Date,
            "k": pl.Int64,
            "pos": pl.Int64,
            "trade_date": pl.Date,
        },
    )


def _extend(cal: list[date], n: int) -> list[date]:
    """The calendar plus the next ``n`` GPW sessions, so an entry ahead can be named."""
    from undercurrent import gpw_calendar

    out = list(cal)
    d = out[-1] if out else date(2000, 1, 3)
    while len(out) < len(cal) + n:
        d += timedelta(days=1)
        if gpw_calendar.is_session(d):
            out.append(d)
    return out


def report_reactions(inp: Inputs) -> pl.DataFrame:
    """One row per report: its window, what of it is known, z and the residual move."""
    w = windows(inp.reports, inp.sessions)
    j = (
        w.join(inp.reports, on="report_id")
        .join(inp.state, on=["isin", "trade_date"], how="left")
        .with_columns(pl.col("liquidity_tier").filter(pl.col("pos") == 0).first().over("report_id"))
    )
    g = j.group_by("report_id", "isin", "signal_key", "day0", "k").agg(
        pl.col("surprise").is_not_null().sum().alias("known"),
        pl.col("surprise").sum().alias("s"),
        pl.col("resid").sum().alias("move"),
        pl.col("liquidity_tier").first().alias("tier"),
    )
    return (
        g.with_columns(
            (pl.col("s") / pl.col("known").cast(pl.Float64).sqrt()).alias("z"),
            (pl.col("known") == pl.col("k")).alias("complete"),
        )
        .drop("s")
        .sort("report_id")
    )


def ordinary(inp: Inputs, reacts: pl.DataFrame) -> dict[int, dict[str, float]]:
    """Mean z², share beyond ±``BIG`` and median |move| on ordinary windows, per length."""
    idx = {d: i for i, d in enumerate(inp.sessions)}
    st = inp.state.filter(pl.col("trade_date").is_in(list(idx))).with_columns(
        pl.col("trade_date").replace_strict(idx, return_dtype=pl.Int64).alias("t")
    )
    near = (
        reacts.filter(pl.col("day0").is_in(list(idx)))
        .select("isin", pl.col("day0").replace_strict(idx, return_dtype=pl.Int64).alias("t0"))
        .unique()
        .with_columns(pl.int_ranges(pl.col("t0") - QUIET, pl.col("t0") + QUIET + 1).alias("t"))
        .explode("t")
        .select("isin", "t")
        .unique()
    )
    seg = study.calendar_segments(inp.sessions)
    ok = (
        st.join(near, on=["isin", "t"], how="anti")
        .filter(pl.col("liquidity_tier").is_in(TIERS) & pl.col("surprise").is_not_null())
        .with_columns(pl.col("t").replace_strict(dict(enumerate(seg.tolist()))).alias("seg"))
        .sort("isin", "t")
    )
    out: dict[int, dict[str, float]] = {}
    for k in range(1, MAX_WINDOW + 1):
        # windows of k consecutive ordinary sessions, inside one calendar segment
        x = ok.with_columns(
            pl.col("surprise").rolling_sum(k).over("isin").alias("s"),
            pl.col("resid").rolling_sum(k).over("isin").alias("m"),
            (pl.col("t") - pl.col("t").shift(k - 1).over("isin")).alias("span"),
            (pl.col("seg") == pl.col("seg").shift(k - 1).over("isin")).alias("same"),
        ).filter((pl.col("span") == k - 1) & pl.col("same"))
        z = (x["s"] / math.sqrt(k)).to_numpy()
        m = x["m"].drop_nulls().to_numpy()
        if not len(z):
            continue
        out[k] = {
            "z2": float(np.mean(z**2)),
            "big": float(np.mean(np.abs(z) > BIG)),
            "move": float(np.median(np.abs(m))) if len(m) else math.nan,
            "n": float(len(z)),
        }
    return out


def kind_stats(reacts: pl.DataFrame, base: dict[int, dict[str, float]]) -> pl.DataFrame:
    """Per kind: how much bigger report windows are than ordinary ones, and the verdict."""
    ev = (
        reacts.filter(
            pl.col("complete") & pl.col("tier").is_in(TIERS) & pl.col("k").is_in(list(base))
        )
        .unique(subset=["signal_key", "isin", "day0"], keep="first", maintain_order=True)
        .sort("signal_key", "isin", "day0")
    )
    rows = []
    for (key,), g in ev.group_by("signal_key", maintain_order=True):
        k = g["k"].to_numpy()
        z = g["z"].to_numpy()
        b = np.array([base[int(x)]["z2"] for x in k])
        x = z**2 / b
        n = len(x)
        t, p = math.nan, math.nan
        if n >= MIN_EVENTS:
            months = [f"{d:%Y-%m}" for d in g["day0"].to_list()]
            t, p, _ = study.cluster_t(x - 1, g["isin"].to_list(), months)
            p = _one_sided(t, p)
        rows.append(
            {
                "signal_key": key,
                "n": n,
                "ratio": float(x.mean()),
                "big_share": float(np.mean(np.abs(z) > BIG)),
                "base_big_share": float(np.mean([base[int(v)]["big"] for v in k])),
                "median_move": float(np.median(np.abs(g["move"].to_numpy()))),
                "base_median_move": float(np.median([base[int(v)]["move"] for v in k])),
                "t_stat": t,
                "p_value": p,
            }
        )
    schema = {
        "signal_key": pl.String,
        "n": pl.Int64,
        "ratio": pl.Float64,
        "big_share": pl.Float64,
        "base_big_share": pl.Float64,
        "median_move": pl.Float64,
        "base_median_move": pl.Float64,
        "t_stat": pl.Float64,
        "p_value": pl.Float64,
    }
    df = pl.DataFrame(rows, schema=schema)
    q = study.benjamini_hochberg(df["p_value"].fill_null(np.nan).to_numpy().astype(np.float64))
    df = df.with_columns(pl.Series("q_value", q).fill_nan(None))
    return df.with_columns(
        pl.when(pl.col("n") < MIN_EVENTS)
        .then(pl.lit("too_few"))
        .when((pl.col("q_value") < SIGNIFICANCE) & (pl.col("ratio") >= MIN_RATIO))
        .then(pl.lit("moves"))
        .otherwise(pl.lit("no_clear_move"))
        .alias("verdict")
    ).sort("ratio", descending=True)


def _one_sided(t: float, p_two: float) -> float:
    """One-sided p that report windows are bigger than ordinary ones, from a two-sided p."""
    if not math.isfinite(t):
        return math.nan
    return p_two / 2 if t > 0 else 1 - p_two / 2


def compute(inp: Inputs) -> tuple[pl.DataFrame, pl.DataFrame, dict[int, dict[str, float]]]:
    """Per-report reactions, per-kind statistics and the ordinary yardstick."""
    reacts = report_reactions(inp)
    base = ordinary(inp, reacts)
    return reacts, kind_stats(reacts, base), base


def run_reactions(duckdb_file: Path, work: Path, day: date) -> Path:
    """Write ``gold/reaction_stats.parquet`` and ``gold/report_reactions.parquet``."""
    as_of = study.as_of_for(day)
    reacts, stats, base = compute(load_inputs(duckdb_file, work, as_of))
    out = work / "gold" / STATS_FILE
    lake.write_frame(stats.with_columns(pl.lit(as_of).alias("as_of")), out)
    lake.write_frame(reacts, work / "gold" / REPORTS_FILE)
    log.info(
        "reactions: %d reports, %d kinds move prices of %d; ordinary z² %s",
        reacts.height,
        stats.filter(pl.col("verdict") == "moves").height,
        stats.height,
        {k: round(v["z2"], 3) for k, v in base.items()},
    )
    return out
