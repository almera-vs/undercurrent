"""Chart setups, tested the way the report signals are (DECISIONS, "Chart setups").

The screener's presets are filters ("RSI below 30", "a new 52-week high"). Whether entering
one of those states has paid off on GPW is a question the event study already answers for
reports, so each preset gets a setup: the session a stock *enters* the preset's state is an
event, and ``study.compute_signal_stats`` measures it with everything the report signals
get: entry at the first close after the digest, round-trip costs, buyable stocks only,
clustered statistics, Benjamini–Hochberg across the setups, the walk-forward and the
deflated Sharpe ratio.

* An event is known when its session's prices are (their ``known_at``), so it reaches that
  evening's digest and the trade enters at the next session's close, like a report's.
* A stock counts a setup again only ``REFRACTORY`` sessions after its last one, so one long
  run above the averages is one event, not twenty.
* Indicators are ``ta.py``'s on adjusted prices, restarting after a hole in the lake.

The setups are their own family: their q-values are adjusted across the setups, not mixed
into the report signals' family, and the report statistics do not change.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from undercurrent import lake, study, ta

log = logging.getLogger(__name__)
FloatArray = npt.NDArray[np.float64]

REFRACTORY = 20
SETUPS = (
    "new_high",
    "new_low",
    "rsi_below_30",
    "rsi_above_70",
    "above_both_averages",
    "golden_cross",
)
# screener preset → setup (site/static/js/screener.js)
PRESET_SETUP = {
    "new_hi": "new_high",
    "new_lo": "new_low",
    "rsi_low": "rsi_below_30",
    "rsi_high": "rsi_above_70",
    "above200": "above_both_averages",
}


def triggers(
    high: FloatArray, low: FloatArray, close: FloatArray, seg: npt.NDArray[np.int64]
) -> dict[str, npt.NDArray[np.bool_]]:
    """For one stock's sessions: where each setup's state begins (before the refractory)."""
    hh, ll = ta.fill_range(high, low, close)
    hi52, lo52 = ta.high_low(hh, ll, seg)
    rsi = ta.rsi(close, ta.RSI_N, seg)
    s50, s200 = ta.sma(close, 50, seg), ta.sma(close, 200, seg)
    with np.errstate(invalid="ignore"):
        state = {
            "new_high": np.isfinite(hi52) & (hh >= hi52),
            "new_low": np.isfinite(lo52) & (ll <= lo52),
            "rsi_below_30": np.isfinite(rsi) & (rsi < 30),
            "rsi_above_70": np.isfinite(rsi) & (rsi > 70),
            "above_both_averages": np.isfinite(s200) & (close > s50) & (close > s200),
            "golden_cross": np.isfinite(s200) & (s50 > s200),
        }
    same = np.concatenate([[False], seg[1:] == seg[:-1]])
    out = {}
    for k, st in state.items():
        prev = np.concatenate([[False], st[:-1]])
        # the state begins: not in it the session before (in the same segment), in it now;
        # a 52-week high is an event every time, the refractory spaces them out
        begins = st & ~(prev & same) if k not in ("new_high", "new_low") else st.copy()
        # the session before must be defined, or the start of the data looks like an entry
        defined_before = np.concatenate([[False], np.isfinite(_level(k, rsi, s200, hi52))[:-1]])
        out[k] = begins & defined_before & same
    return out


def _level(k: str, rsi: FloatArray, s200: FloatArray, hi52: FloatArray) -> FloatArray:
    if k.startswith("rsi"):
        return rsi
    if k in ("above_both_averages", "golden_cross"):
        return s200
    return hi52


def _refractory(hit: npt.NDArray[np.bool_], gap: int = REFRACTORY) -> list[int]:
    out: list[int] = []
    for i in np.nonzero(hit)[0].tolist():
        if not out or i - out[-1] >= gap:
            out.append(i)
    return out


def setup_events(prices: pl.DataFrame, segment: dict[date, int]) -> pl.DataFrame:
    """Every setup event in ``prices``, as study events (signal type ``setup``).

    ``prices``: isin, trade_date, market, close, adj_close, high, low, known_at; the setup is
    the event's subtype. ``segment``: each session's calendar segment (it goes up after
    every hole in the lake).
    """
    rows: list[dict[str, object]] = []
    for (isin,), g in prices.sort(["isin", "trade_date"]).group_by(["isin"], maintain_order=True):
        dates = g["trade_date"].to_list()
        seg = np.array([segment.get(d, -1) for d in dates], dtype=np.int64)
        c = g["close"].to_numpy().astype(np.float64)
        f = g["adj_close"].to_numpy().astype(np.float64) / c
        f = np.where(np.isfinite(f) & (f > 0), f, 1.0)
        h = g["high"].fill_null(np.nan).to_numpy().astype(np.float64) * f
        lo = g["low"].fill_null(np.nan).to_numpy().astype(np.float64) * f
        known = g["known_at"].to_list()
        market = g["market"].to_list()
        for k, hit in triggers(h, lo, c * f, seg).items():
            for i in _refractory(hit):
                rows.append(
                    {
                        "report_id": f"setup:{k}:{isin}:{dates[i]:%Y%m%d}",
                        "signal_type": "setup",
                        "subtype": k,
                        "insider_side": None,
                        "isin": str(isin),
                        "known_at": known[i],
                        "event_date": dates[i],
                        "market": market[i],
                    }
                )
    schema: dict[str, Any] = {
        "report_id": pl.String,
        "signal_type": pl.String,
        "subtype": pl.String,
        "insider_side": pl.String,
        "isin": pl.String,
        "known_at": pl.Datetime("us", "UTC"),
        "event_date": pl.Date,
        "market": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def run_setups(duckdb_file: Path, work: Path, day: date) -> Path:
    """Write ``gold/setup_stats.parquet`` and ``gold/setup_paths.parquet`` for ``day``."""
    import duckdb

    as_of = study.as_of_for(day)
    con = duckdb.connect(str(duckdb_file))
    try:
        prices = con.sql(
            "select a.isin, a.trade_date, a.market, a.close, a.adj_close, p.high, p.low, "
            "a.known_at from adjusted_prices a left join prices_daily p using (isin, trade_date) "
            "where a.trade_date <= ?",
            params=[day],
        ).pl()
        holes = con.sql("select trade_date, after_hole from sessions order by 1").fetchall()
    finally:
        con.close()
    base = study.load_inputs(duckdb_file, as_of)
    seg = np.cumsum([bool(h) for _, h in holes]).tolist()
    events = setup_events(prices, {d: int(k) for (d, _), k in zip(holes, seg, strict=True)})
    inputs = study.cut_inputs(events, base.returns, base.benchmark, as_of)
    stats, paths = study.compute_signal_stats(inputs)
    out = work / "gold" / "setup_stats.parquet"
    lake.write_frame(stats, out)
    lake.write_frame(paths, work / "gold" / "setup_paths.parquet")
    log.info("setup_stats: %d events, %d rows", inputs.events.height, stats.height)
    return out
