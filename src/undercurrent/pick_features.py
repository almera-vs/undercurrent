"""Point-in-time features for the stock of the week (``pick.py``).

The panel is a stocks × sessions grid of daily returns, closes and turnover on the main
market, with WIG and the signal events. Features for a rebalance session ``t`` use only
sessions up to and including ``t`` (its close); the 20-session target uses ``t+1 … t+20``.
Nothing reaches across a hole in the calendar (``study.calendar_segments``).

Features, all measured against WIG where they are returns:

* trend: 12-1 and 6-1 month momentum, 1-month reversal, distance from the 52-week high;
* risk: 60-session volatility, 250-session beta and residual volatility, 250-session drawdown;
* liquidity: median turnover, share of sessions without a trade, Amihud illiquidity;
* news flow: signal events of the last 60 sessions by kind, insider net buying of the last
  20 sessions in days of turnover, corporate actions.

No financial statements, share counts or market values exist in the lake, so there are no
valuation features.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy import stats as sps

from undercurrent.study import (
    LookAheadError,
    buyable,
    calendar_segments,
    signal_key,
)

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

HORIZON = 20  # sessions the target looks ahead
HISTORY = 260  # continuous sessions a stock needs before it can be scored
YEAR = 252
MIN_RETURNS = 200  # of the last YEAR sessions, at least this many with a return
LIQ = 60
MIN_TURNOVER_PICK = 100_000.0  # zł a day, median over LIQ sessions (study: 10,000)

# News groups: signal key prefix → feature name.
NEWS_GROUPS: dict[str, tuple[str, ...]] = {
    "news_insider_buy": ("insider_tx:buy",),
    "news_insider_sell": ("insider_tx:sell",),
    "news_holder": ("holder_threshold",),
    "news_profit_warning": ("inside_info:profit_warning",),
    "news_contract": ("inside_info:large_contract",),
    "news_financing": ("inside_info:financing",),
    "news_buyback": ("inside_info:buyback", "inside_info:dividend_change"),
    "news_m_and_a": ("inside_info:m_and_a",),
    "news_other": (
        "inside_info:other",
        "inside_info:strategy",
        "inside_info:litigation",
        "inside_info:rating",
        "insider_tx:unknown",
    ),
}

FEATURES: tuple[str, ...] = (
    "mom_12_1",
    "mom_6_1",
    "rev_1m",
    "off_high",
    "vol_60",
    "beta",
    "resid_vol",
    "drawdown",
    "turnover",
    "no_trade",
    "illiquidity",
    *NEWS_GROUPS,
    "insider_net",
    "corp_actions",
)


@dataclass(frozen=True)
class PickInputs:
    """Point-in-time inputs for the pick: main-market prices, WIG and signal events."""

    as_of: datetime
    prices: pl.DataFrame  # isin, trade_date, ticker, close, adj_ret, value_pln, ca, known_at
    benchmark: pl.DataFrame  # trade_date, wig_ret, known_at
    events: pl.DataFrame  # isin, event_date, signal_key, insider_value, known_at


@dataclass
class Panel:
    """Stocks × sessions arrays built from :class:`PickInputs`."""

    cal: list[date]
    seg: npt.NDArray[np.int64]
    isins: list[str]
    tickers: list[str]
    ret: FloatArray  # daily adjusted return, NaN where unknown
    close: FloatArray  # raw close, NaN where no row
    turnover: FloatArray  # zł, 0 where no trade or no row
    mkt: FloatArray  # WIG daily return
    cum: FloatArray  # stocks × (T+1) prefix sums of log(1 + ret), unknown days as 0
    cum_mkt: FloatArray  # (T+1) prefix sums of log(1 + WIG)
    n_ret: npt.NDArray[np.int64]  # stocks × (T+1) prefix counts of known returns
    news: dict[str, FloatArray]  # feature → stocks × (T+1) prefix sums of daily counts
    insider_value: FloatArray  # stocks × (T+1) prefix sums of signed insider value
    corp: FloatArray  # stocks × (T+1) prefix sums of corporate-action days


def load_inputs(duckdb_file: Path, as_of: datetime) -> PickInputs:
    """Read the gold tables and cut them at ``as_of`` by ``known_at``."""
    import duckdb

    con = duckdb.connect(str(duckdb_file))
    try:
        prices = con.sql(
            "select isin, trade_date, ticker, close, adj_ret, coalesce(value_pln, 0) as value_pln, "
            "(factor_source is not null)::int as ca, known_at from adjusted_prices "
            "where market = 'GPW'"
        ).pl()
        bench = con.sql(
            "select b.trade_date, b.wig_ret, s.known_at from benchmark_returns b "
            "join benchmark s using (trade_date) where b.wig_ret is not null"
        ).pl()
        events = con.sql(
            "select isin, event_date, signal_type, subtype, insider_side, insider_value, "
            "known_at from events where isin is not null and event_date is not null"
        ).pl()
    finally:
        con.close()
    return cut_inputs(prices, bench, events, as_of)


def cut_inputs(
    prices: pl.DataFrame, benchmark: pl.DataFrame, events: pl.DataFrame, as_of: datetime
) -> PickInputs:
    """Keep rows known by ``as_of`` and give events their signal key."""
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))

    def cut(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC"))).filter(
            pl.col("known_at") <= ts
        )

    ev = cut(events)
    if "signal_key" not in ev.columns:
        ev = ev.with_columns(
            pl.struct("signal_type", "subtype", "insider_side")
            .map_elements(
                lambda s: signal_key(s["signal_type"], s["subtype"], s["insider_side"]),
                return_dtype=pl.String,
            )
            .alias("signal_key")
        )
    if "insider_value" not in ev.columns:
        ev = ev.with_columns(pl.lit(None, dtype=pl.Float64).alias("insider_value"))
    return PickInputs(
        as_of=as_of,
        prices=cut(prices).sort(["isin", "trade_date"]),
        benchmark=cut(benchmark).sort("trade_date"),
        events=ev.select("isin", "event_date", "signal_key", "insider_value", "known_at"),
    )


def assert_point_in_time(inputs: PickInputs) -> None:
    """Raise :class:`study.LookAheadError` if any row is known after ``as_of``."""
    for name, df in (
        ("prices", inputs.prices),
        ("benchmark", inputs.benchmark),
        ("events", inputs.events),
    ):
        if df.height and df["known_at"].max() > inputs.as_of:  # type: ignore[operator]
            raise LookAheadError(f"{name} has rows known after {inputs.as_of.isoformat()}")


def _prefix(a: FloatArray) -> FloatArray:
    """Prefix sums along sessions with a leading zero column: sum(lo..hi) = p[hi+1] - p[lo]."""
    out = np.zeros((a.shape[0], a.shape[1] + 1))
    np.cumsum(a, axis=1, out=out[:, 1:])
    return out


def build_panel(inputs: PickInputs) -> Panel:
    """Arrange the inputs on the session calendar (WIG's trading days)."""
    assert_point_in_time(inputs)
    cal = inputs.benchmark["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(cal)}
    t_n = len(cal)
    mkt = inputs.benchmark["wig_ret"].to_numpy().astype(np.float64)
    isins = sorted(set(inputs.prices["isin"].to_list()))
    pos = {s: i for i, s in enumerate(isins)}
    n = len(isins)
    ret = np.full((n, t_n), np.nan)
    close = np.full((n, t_n), np.nan)
    turnover = np.zeros((n, t_n))
    corp = np.zeros((n, t_n))
    tickers = ["" for _ in isins]
    p = inputs.prices
    for isin, d, tk, cl, r, v, ca in zip(
        p["isin"].to_list(),
        p["trade_date"].to_list(),
        p["ticker"].to_list(),
        p["close"].to_list(),
        p["adj_ret"].to_list(),
        p["value_pln"].to_list(),
        p["ca"].to_list(),
        strict=True,
    ):
        j = idx.get(d)
        if j is None:
            continue
        i = pos[isin]
        tickers[i] = tk or tickers[i]
        if cl is not None:
            close[i, j] = cl
        if r is not None and math.isfinite(r):
            ret[i, j] = r
        turnover[i, j] = v or 0.0
        corp[i, j] = ca or 0
    news_daily = {name: np.zeros((n, t_n)) for name in NEWS_GROUPS}
    key_to_feature = {k: name for name, keys in NEWS_GROUPS.items() for k in keys}
    insider = np.zeros((n, t_n))
    e = inputs.events
    for isin, d, key, val in zip(
        e["isin"].to_list(),
        e["event_date"].to_list(),
        e["signal_key"].to_list(),
        e["insider_value"].to_list(),
        strict=True,
    ):
        row, j = pos.get(isin), idx.get(d)
        if row is None or j is None:
            continue
        feat = key_to_feature.get(key)
        if feat:
            news_daily[feat][row, j] += 1
        if val is not None and key in ("insider_tx:buy", "insider_tx:sell"):
            insider[row, j] += val if key.endswith("buy") else -val
    lr = np.where(np.isfinite(ret), np.log1p(np.where(np.isfinite(ret), ret, 0.0)), 0.0)
    cum_mkt = np.concatenate([[0.0], np.cumsum(np.log1p(mkt))])
    n_ret = np.zeros((n, t_n + 1), dtype=np.int64)
    np.cumsum(np.isfinite(ret), axis=1, out=n_ret[:, 1:])
    return Panel(
        cal=cal,
        seg=calendar_segments(cal),
        isins=isins,
        tickers=tickers,
        ret=ret,
        close=close,
        turnover=turnover,
        mkt=mkt,
        cum=_prefix(lr),
        cum_mkt=cum_mkt,
        n_ret=n_ret,
        news={k: _prefix(v) for k, v in news_daily.items()},
        insider_value=_prefix(insider),
        corp=_prefix(corp),
    )


def rebalance_positions(panel: Panel) -> list[int]:
    """Calendar positions of the first session of every ISO week."""
    out = []
    for t, d in enumerate(panel.cal):
        if t == 0 or panel.cal[t - 1].isocalendar()[:2] != d.isocalendar()[:2]:
            out.append(t)
    return out


def _excess_log(panel: Panel, lo: int, hi: int) -> FloatArray:
    """Stock minus WIG log return over sessions lo..hi (inclusive), per stock."""
    stock = panel.cum[:, hi + 1] - panel.cum[:, lo]
    market = panel.cum_mkt[hi + 1] - panel.cum_mkt[lo]
    return np.asarray(stock - market, dtype=np.float64)


def eligible(panel: Panel, t: int) -> BoolArray:
    """Stocks that can be scored at ``t``: a year of continuous history, liquid enough."""
    n = len(panel.isins)
    if t < HISTORY - 1 or panel.seg[t - HISTORY + 1] != panel.seg[t]:
        return np.zeros(n, dtype=bool)
    known = panel.n_ret[:, t + 1] - panel.n_ret[:, t + 1 - YEAR]
    ok = known >= MIN_RETURNS
    lo = t - LIQ + 1
    for i in np.nonzero(ok)[0]:
        ok[i] = buyable(
            panel.close[i, lo : t + 1], panel.turnover[i, lo : t + 1], MIN_TURNOVER_PICK
        )
    return np.asarray(ok, dtype=bool)


def raw_features(panel: Panel, t: int) -> FloatArray:
    """Stocks × FEATURES at session ``t`` (meaningful only for eligible stocks)."""
    n = len(panel.isins)
    out = np.full((n, len(FEATURES)), np.nan)
    col = {f: k for k, f in enumerate(FEATURES)}
    out[:, col["mom_12_1"]] = _excess_log(panel, t - YEAR + 1, t - 21)
    out[:, col["mom_6_1"]] = _excess_log(panel, t - 125, t - 21)
    out[:, col["rev_1m"]] = _excess_log(panel, t - 19, t)
    log_p = panel.cum[:, t - YEAR + 2 : t + 2]  # log price index after each session
    out[:, col["off_high"]] = log_p[:, -1] - log_p.max(axis=1)
    run_max = np.maximum.accumulate(log_p, axis=1)
    out[:, col["drawdown"]] = (run_max - log_p).max(axis=1)
    r60 = panel.ret[:, t - LIQ + 1 : t + 1]
    with np.errstate(invalid="ignore"):
        out[:, col["vol_60"]] = np.nanstd(r60, axis=1)
    y = panel.ret[:, t - YEAR + 1 : t + 1]
    x = panel.mkt[t - YEAR + 1 : t + 1]
    m = np.isfinite(y)
    cnt = m.sum(axis=1)
    xs = np.where(m, x, 0.0)
    ys = np.where(m, y, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my = xs.sum(1) / cnt, ys.sum(1) / cnt
        cov = (xs * ys).sum(1) / cnt - mx * my
        var = (xs * xs).sum(1) / cnt - mx * mx
        beta = cov / var
        resid = np.where(m, y - beta[:, None] * x, np.nan)
        out[:, col["beta"]] = beta
        out[:, col["resid_vol"]] = np.nanstd(resid, axis=1)
    tv = panel.turnover[:, t - LIQ + 1 : t + 1]
    med_tv = np.median(tv, axis=1)
    out[:, col["turnover"]] = np.log1p(med_tv)
    out[:, col["no_trade"]] = (tv <= 0).mean(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where((tv > 0) & np.isfinite(r60), np.abs(r60) / tv, np.nan)
        out[:, col["illiquidity"]] = np.log1p(np.nanmean(ratio, axis=1) * 1e6)
    for name, pre in panel.news.items():
        out[:, col[name]] = pre[:, t + 1] - pre[:, t + 1 - LIQ]
    net = panel.insider_value[:, t + 1] - panel.insider_value[:, t + 1 - HORIZON]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, col["insider_net"]] = np.where(med_tv > 0, net / med_tv, 0.0)
    out[:, col["corp_actions"]] = panel.corp[:, t + 1] - panel.corp[:, t + 1 - LIQ]
    return out


def rank_normal(x: FloatArray) -> FloatArray:
    """Column-wise rank → standard normal scores; NaN becomes 0 (the cross-sectional middle)."""
    out = np.zeros_like(x)
    n = x.shape[0]
    if n == 0:
        return out
    for k in range(x.shape[1]):
        v = x[:, k]
        ok = np.isfinite(v)
        if ok.sum() < 2:
            continue
        r = sps.rankdata(v[ok])
        out[ok, k] = sps.norm.ppf((r - 0.5) / ok.sum())
    return out


def forward_excess(panel: Panel, t: int, h: int = HORIZON) -> FloatArray:
    """Compounded stock minus WIG return over sessions t+1..t+h; NaN if not all known."""
    n = len(panel.isins)
    if t + h >= len(panel.cal) or panel.seg[t + h] != panel.seg[t]:
        return np.full(n, np.nan)
    known = panel.n_ret[:, t + h + 1] - panel.n_ret[:, t + 1]
    stock = np.exp(panel.cum[:, t + h + 1] - panel.cum[:, t + 1]) - 1
    market = math.exp(panel.cum_mkt[t + h + 1] - panel.cum_mkt[t + 1]) - 1
    return np.where(known == h, stock - market, np.nan)


def hold_excess(panel: Panel, i: int, t: int, t2: int) -> float:
    """Stock ``i`` minus WIG, compounded, from the close of ``t`` to the close of ``t2``.

    A session without a trade counts as an unchanged price.
    """
    if t2 <= t or panel.seg[t2] != panel.seg[t]:
        return math.nan
    stock = math.exp(panel.cum[i, t2 + 1] - panel.cum[i, t + 1]) - 1
    market = math.exp(panel.cum_mkt[t2 + 1] - panel.cum_mkt[t + 1]) - 1
    return float(stock - market)
