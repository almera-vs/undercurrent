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
SQUEEZE_SESSIONS = 126  # six months: the Bollinger width at its lowest of these is a squeeze
SLOPE_SESSIONS = 10  # an average "rises" when it is above its value this many sessions ago
SETUPS = (
    "new_high",
    "new_low",
    "rsi_below_30",
    "rsi_above_70",
    "above_both_averages",
    "golden_cross",
    "pullback",
    "squeeze",
    "breakout",
    "oversold",
    "trend_health",
)
# screener preset → setup. The screener's presets are these states, computed here, so a
# preset and the setup tested on the Signals page can never differ.
PRESET_SETUP = {
    "new_hi": "new_high",
    "new_lo": "new_low",
    "rsi_low": "rsi_below_30",
    "rsi_high": "rsi_above_70",
    "above200": "above_both_averages",
    "pullback": "pullback",
    "squeeze": "squeeze",
    "breakout": "breakout",
    "oversold": "oversold",
    "trend": "trend_health",
    "cheap": "cheap_vs_sector",
    "quality": "quality",
    "distress": "distress",
}
# Setups from the valuation (valuation.history_events): cross-sectional, judged weekly, so
# they are not among the per-stock ``states``.
VALUATION_SETUPS = ("cheap_vs_sector", "quality", "distress")
# 52-week highs and lows are events every time they happen (the refractory spaces them);
# every other setup is an event when its state begins
EVERY_TIME = ("new_high", "new_low")


def _lag(x: FloatArray, k: int, seg: npt.NDArray[np.int64]) -> FloatArray:
    """``x`` ``k`` sessions earlier in the same segment (NaN otherwise)."""
    out = np.full(len(x), np.nan)
    if len(x) > k:
        out[k:] = np.where(seg[k:] == seg[:-k], x[:-k], np.nan)
    return out


def states(
    high: FloatArray,
    low: FloatArray,
    close: FloatArray,
    turnover: FloatArray,
    seg: npt.NDArray[np.int64],
) -> tuple[dict[str, npt.NDArray[np.bool_]], dict[str, npt.NDArray[np.bool_]]]:
    """For one stock's sessions: whether it is in each setup's state, and whether it can tell.

    The second dict says where every input of a state is defined: a state that cannot be
    judged yet (too little history) is not the same as being out of it.

    * ``new_high`` / ``new_low``: today's high (low) is the highest (lowest) of 250 sessions;
    * ``rsi_below_30`` / ``rsi_above_70``: Wilder's RSI 14;
    * ``above_both_averages``: close above SMA50 and SMA200; ``golden_cross``: SMA50 > SMA200;
    * ``pullback``: close above SMA200 with RSI between 30 and 50;
    * ``squeeze``: Bollinger width at its lowest of ``SQUEEZE_SESSIONS``, RSI 45–55;
    * ``breakout``: close within 3 % of the 52-week high, turnover 1.5× its 20-session mean,
      RSI above 55;
    * ``oversold``: RSI below 30, close within 10 % of the 52-week low, turnover 1.5× normal;
    * ``trend_health``: close > SMA50 > SMA200, both averages rising over ``SLOPE_SESSIONS``,
      ADX above 25.
    """
    hh, ll = ta.fill_range(high, low, close)
    hi52, lo52 = ta.high_low(hh, ll, seg)
    rsi = ta.rsi(close, ta.RSI_N, seg)
    s50, s200 = ta.sma(close, 50, seg), ta.sma(close, 200, seg)
    bbw = ta.bollinger_width(close, seg)
    bbw_min = ta.rolling_min(bbw, SQUEEZE_SESSIONS, seg)
    rvol = ta.relative(turnover, ta.RVOL_N, seg)
    adx, _, _ = ta.adx(hh, ll, close, ta.ADX_N, seg)
    s50_ago, s200_ago = _lag(s50, SLOPE_SESSIONS, seg), _lag(s200, SLOPE_SESSIONS, seg)
    f = np.isfinite
    with np.errstate(invalid="ignore"):
        state = {
            "new_high": hh >= hi52,
            "new_low": ll <= lo52,
            "rsi_below_30": rsi < 30,
            "rsi_above_70": rsi > 70,
            "above_both_averages": (close > s50) & (close > s200),
            "golden_cross": s50 > s200,
            "pullback": (close > s200) & (rsi >= 30) & (rsi <= 50),
            "squeeze": (bbw <= bbw_min) & (rsi >= 45) & (rsi <= 55),
            "breakout": (close >= 0.97 * hi52) & (rvol > 1.5) & (rsi > 55),
            "oversold": (rsi < 30) & (close <= 1.10 * lo52) & (rvol > 1.5),
            "trend_health": (close > s50)
            & (s50 > s200)
            & (s50 > s50_ago)
            & (s200 > s200_ago)
            & (adx > 25),
        }
        defined = {
            "new_high": f(hi52),
            "new_low": f(lo52),
            "rsi_below_30": f(rsi),
            "rsi_above_70": f(rsi),
            "above_both_averages": f(s200),
            "golden_cross": f(s200),
            "pullback": f(s200) & f(rsi),
            "squeeze": f(bbw_min) & f(rsi),
            "breakout": f(hi52) & f(rvol) & f(rsi),
            "oversold": f(lo52) & f(rvol) & f(rsi),
            "trend_health": f(s200_ago) & f(adx),
        }
    return {k: v & defined[k] for k, v in state.items()}, defined


def triggers(
    high: FloatArray,
    low: FloatArray,
    close: FloatArray,
    turnover: FloatArray,
    seg: npt.NDArray[np.int64],
) -> dict[str, npt.NDArray[np.bool_]]:
    """For one stock's sessions: where each setup's state begins (before the refractory).

    A state begins where the stock is in it and was not the session before, in the same
    segment, with the state defined then too (the start of the data is not an entry).
    """
    state, defined = states(high, low, close, turnover, seg)
    same = np.concatenate([[False], seg[1:] == seg[:-1]])
    out = {}
    for k, st in state.items():
        prev = np.concatenate([[False], st[:-1]])
        defined_before = np.concatenate([[False], defined[k][:-1]])
        begins = st.copy() if k in EVERY_TIME else st & ~prev
        out[k] = begins & defined_before & same
    return out


def _refractory(hit: npt.NDArray[np.bool_], gap: int = REFRACTORY) -> list[int]:
    out: list[int] = []
    for i in np.nonzero(hit)[0].tolist():
        if not out or i - out[-1] >= gap:
            out.append(i)
    return out


def setup_events(prices: pl.DataFrame, segment: dict[date, int]) -> pl.DataFrame:
    """Every setup event in ``prices``, as study events (signal type ``setup``).

    ``prices``: isin, trade_date, market, close, adj_close, high, low, value_pln, known_at;
    the setup is the event's subtype. ``segment``: each session's calendar segment (it goes up after
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
        tv = g["value_pln"].fill_null(0.0).to_numpy().astype(np.float64)
        known = g["known_at"].to_list()
        market = g["market"].to_list()
        for k, hit in triggers(h, lo, c * f, tv, seg).items():
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


def run_setups(duckdb_file: Path, work: Path, day: date, data_dir: Path | None = None) -> Path:
    """Write ``gold/setup_stats.parquet`` and ``gold/setup_paths.parquet`` for ``day``.

    With ``data_dir``, the valuation setups use GPW's latest sector codes from the lake;
    without, they rank every stock against the whole market.
    """
    import duckdb

    from undercurrent import esef, indicators, valuation

    as_of = study.as_of_for(day)
    con = duckdb.connect(str(duckdb_file))
    try:
        prices = con.sql(
            "select a.isin, a.trade_date, a.market, a.close, a.adj_close, p.high, p.low, "
            "a.value_pln, a.known_at from adjusted_prices a "
            "left join prices_daily p using (isin, trade_date) "
            "where a.trade_date <= ?",
            params=[day],
        ).pl()
        holes = con.sql("select trade_date, after_hole from sessions order by 1").fetchall()
        tables = {
            r[0] for r in con.sql("select table_name from information_schema.tables").fetchall()
        }
        facts = con.sql("select * from fundamentals").pl() if "fundamentals" in tables else None
    finally:
        con.close()
    base = study.load_inputs(duckdb_file, as_of)
    seg = np.cumsum([bool(h) for _, h in holes]).tolist()
    events = setup_events(prices, {d: int(k) for (d, _), k in zip(holes, seg, strict=True)})
    if facts is not None and facts.height:
        sectors: dict[str, tuple[int | None, str | None]] = {}
        gpw = indicators.latest(data_dir, day) if data_dir is not None else None
        if gpw is not None:
            sectors = {
                r["isin"]: (r["sector_code"], r["sector"]) for r in gpw.iter_rows(named=True)
            }
        main = prices.filter(pl.col("market") == "GPW").select(
            "isin", "trade_date", "close", "known_at", "market"
        )
        cal = [d for d, _ in holes if d <= day]
        events = pl.concat(
            [events, valuation.history_events(main, esef.statements(facts), sectors, cal)]
        )
    inputs = study.cut_inputs(events, base.returns, base.benchmark, as_of)
    stats, paths = study.compute_signal_stats(inputs)
    out = work / "gold" / "setup_stats.parquet"
    lake.write_frame(stats, out)
    lake.write_frame(paths, work / "gold" / "setup_paths.parquet")
    log.info("setup_stats: %d events, %d rows", inputs.events.height, stats.height)
    return out
