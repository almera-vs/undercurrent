"""The market state (L2 in DECISIONS, "One source of truth"): one row per (stock, session).

Every column is known at that session's close and is computed from the layers above it
only: adjusted prices, raw high/low for spreads, and the index returns. The forecast, the
event pages and the pick read their volatility, betas, surprise, regime, liquidity and costs
from here instead of computing their own.

Columns of ``gold/market_state.parquet``:

* ``sigma``, ``sigma_20``: forecast standard deviation of the next session's return and of
  the next 20 sessions' return (``vol``); ``sigma_source`` is ``garch`` or ``ewma``.
* ``beta_wig``, ``beta_tbsp``: Kalman betas to WIG and the treasury bond index (``beta``).
* ``resid``: the session's return the factors did not predict; ``surprise`` is the same in
  standard deviations of its forecast.
* ``regime_stress``: filtered probability that the market is in its stress state
  (``regime``); the same value for every stock on a session.
* ``median_turnover``, ``median_close``, ``liquidity_tier``: the study's buyability rule over
  the last ``LIQ`` sessions, at both thresholds (``thin`` / ``tradable`` / ``liquid``). It is
  the one liquidity rule; the study's and the pick's floors are its two tiers.
* ``spread``: effective bid-ask spread (Abdi–Ranaldo) over the last ``LIQ`` sessions;
  ``spread_source`` is ``stock`` or ``market`` (the market-wide figure the study uses, when
  the stock has too few traded pairs).
* ``cost``: round-trip cost of a ``TICKET`` zł order: one spread, commission both ways and
  square-root market impact ``Y · σ · √(ticket / median turnover)`` both ways.
* ``signal_effect``: what the stock's recent reports are expected to add against WIG over
  the rest of their 20-session windows, each kind's event-study result shrunk by its
  evidence (``signal_effect.py``); 0 with no report running, missing without the study.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl

from undercurrent import beta as beta_mod
from undercurrent import lake, regime, signal_effect, study, vol

log = logging.getLogger(__name__)
FloatArray = npt.NDArray[np.float64]

LIQ = study.LIQ_WINDOW
MIN_TURNOVER_LIQUID = 100_000.0  # the pick's floor (pick_features.MIN_TURNOVER_PICK)
MIN_PAIRS = 20  # traded session pairs a stock needs for its own spread estimate
MARKET_SPREAD = {"GPW": 0.016, "NC": 0.048}  # METHODOLOGY § 4, event-weighted
COMMISSION = 0.002  # each way
TICKET = 10_000.0  # zł; the study's turnover floor: an ordinary retail order
IMPACT_Y = 1.0
MAX_COST = 0.25
HORIZON = 20
FACTORS = ("WIG", "TBSP.INDEX")


@dataclass(frozen=True)
class StateInputs:
    """Point-in-time inputs: prices of both markets, raw highs/lows, index returns."""

    as_of: datetime
    prices: pl.DataFrame  # isin, trade_date, market, close, adj_ret, value_pln, volume,
    # high, low, ca, known_at
    indices: pl.DataFrame  # name, trade_date, ret, known_at
    sessions: pl.DataFrame  # trade_date, known_at


def load_inputs(duckdb_file: Path, as_of: datetime) -> StateInputs:
    """Read the gold tables and cut them at ``as_of`` by ``known_at``."""
    import duckdb

    con = duckdb.connect(str(duckdb_file), read_only=True)
    try:
        prices = con.sql(
            "select a.isin, a.trade_date, a.market, a.close, a.adj_ret, "
            "coalesce(a.value_pln, 0) as value_pln, coalesce(a.volume, 0) as volume, "
            "p.open, p.high, p.low, (a.factor_source is not null)::int as ca, a.known_at "
            "from adjusted_prices a left join prices_daily p using (isin, trade_date)"
        ).pl()
        indices = con.sql("select name, trade_date, ret, known_at from index_returns").pl()
        sessions = con.sql("select trade_date, known_at from benchmark").pl()
    finally:
        con.close()
    return cut_inputs(prices, indices, sessions, as_of)


def cut_inputs(
    prices: pl.DataFrame, indices: pl.DataFrame, sessions: pl.DataFrame, as_of: datetime
) -> StateInputs:
    """Keep the rows known by ``as_of``."""
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))

    def cut(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC"))).filter(
            pl.col("known_at") <= ts
        )

    return StateInputs(
        as_of=as_of,
        prices=cut(prices).sort(["isin", "trade_date"]),
        indices=cut(indices).sort(["name", "trade_date"]),
        sessions=cut(sessions).unique("trade_date").sort("trade_date"),
    )


@dataclass
class Grid:
    """Stocks × sessions arrays on the WIG session calendar."""

    cal: list[date]
    seg: npt.NDArray[np.int64]
    isins: list[str]
    market: list[str]
    has_row: npt.NDArray[np.bool_]
    ret: FloatArray
    close: FloatArray
    turnover: FloatArray  # NaN before the stock's first row, 0 on a session without one
    traded: npt.NDArray[np.bool_]
    log_c: FloatArray
    eta: FloatArray  # mean of log high and log low
    rv: FloatArray  # the session's range variance (vol.range_variance), NaN without prices
    ca: npt.NDArray[np.bool_]
    known_at: list[datetime | None]  # per session: when its prices were known
    index: dict[str, FloatArray]  # index name → returns on the calendar


def build_grid(inputs: StateInputs) -> Grid:
    """Arrange the inputs on the session calendar."""
    if inputs.prices.height and inputs.prices["known_at"].max() > inputs.as_of:  # type: ignore[operator]
        raise study.LookAheadError("prices known after as_of")
    cal = inputs.sessions["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(cal)}
    t_n = len(cal)
    p = inputs.prices.filter(pl.col("trade_date").is_in(cal))
    isins = sorted(set(p["isin"].to_list()))
    pos = {s: i for i, s in enumerate(isins)}
    n = len(isins)
    shape = (n, t_n)
    has_row = np.zeros(shape, dtype=bool)
    ret = np.full(shape, np.nan)
    close = np.full(shape, np.nan)
    value = np.zeros(shape)
    traded = np.zeros(shape, dtype=bool)
    log_c = np.full(shape, np.nan)
    eta = np.full(shape, np.nan)
    ca = np.zeros(shape, dtype=bool)
    market = ["" for _ in isins]
    rows = pos_rows = np.array([pos[s] for s in p["isin"].to_list()], dtype=np.int64)
    cols = np.array([idx[d] for d in p["trade_date"].to_list()], dtype=np.int64)
    has_row[rows, cols] = True

    def arr(c: str) -> FloatArray:
        return p[c].cast(pl.Float64).fill_null(np.nan).to_numpy()

    cl, r, v, vo, hi, lo = (
        arr("close"),
        arr("adj_ret"),
        arr("value_pln"),
        arr("volume"),
        arr("high"),
        arr("low"),
    )
    op = arr("open") if "open" in p.columns else np.full(len(cl), np.nan)
    close[rows, cols] = cl
    ret[rows, cols] = np.where(np.isfinite(r), r, np.nan)
    value[rows, cols] = np.where(np.isfinite(v), v, 0.0)
    traded[rows, cols] = np.isfinite(vo) & (vo > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = (cl > 0) & (hi > 0) & (lo > 0)
        log_c[rows, cols] = np.where(ok, np.log(np.where(ok, cl, 1.0)), np.nan)
        eta[rows, cols] = np.where(
            ok, (np.log(np.where(ok, hi, 1.0)) + np.log(np.where(ok, lo, 1.0))) / 2, np.nan
        )
    ca[rows, cols] = p["ca"].to_numpy().astype(bool)
    rv = np.full(shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        good = (op > 0) & (hi > 0) & (lo > 0) & (cl > 0) & (vo > 0)

        def lg(x: FloatArray) -> FloatArray:
            return np.asarray(np.log(np.where(good, x, 1.0)), dtype=np.float64)

        day_rv = vol.range_variance(lg(op), lg(hi), lg(lo), lg(cl), r)
    rv[rows, cols] = np.where(good, day_rv, np.nan)
    for i, m in zip(pos_rows, p["market"].to_list(), strict=True):
        market[i] = m or market[i]
    started = np.maximum.accumulate(has_row, axis=1)
    turnover = np.where(started, value, np.nan)
    known = dict(zip(inputs.sessions["trade_date"], inputs.sessions["known_at"], strict=True))
    index: dict[str, FloatArray] = {}
    for name, grp in inputs.indices.group_by("name", maintain_order=True):
        a = np.full(t_n, np.nan)
        for d, x in zip(grp["trade_date"].to_list(), grp["ret"].to_list(), strict=True):
            j = idx.get(d)
            if j is not None and x is not None and math.isfinite(x):
                a[j] = x
        index[str(name[0])] = a
    return Grid(
        cal=cal,
        seg=study.calendar_segments(cal),
        isins=isins,
        market=market,
        has_row=has_row,
        ret=ret,
        close=close,
        turnover=turnover,
        traded=traded,
        log_c=log_c,
        eta=eta,
        ca=ca,
        rv=rv,
        known_at=[known.get(d) for d in cal],
        index=index,
    )


def _window_starts(seg: npt.NDArray[np.int64], width: int) -> npt.NDArray[np.int64]:
    """First position of the trailing window of ``width`` sessions that stays in a segment."""
    t_n = len(seg)
    first = np.zeros(t_n, dtype=np.int64)
    for t in range(1, t_n):
        first[t] = first[t - 1] if seg[t] == seg[t - 1] else t
    return np.maximum(np.arange(t_n) - width + 1, first)


def liquidity(g: Grid) -> tuple[FloatArray, FloatArray]:
    """Median close and median daily turnover over the last ``LIQ`` sessions."""
    n, t_n = g.ret.shape
    med_close = np.full((n, t_n), np.nan)
    med_tv = np.full((n, t_n), np.nan)
    lo = _window_starts(g.seg, LIQ)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for t in range(t_n):
                med_close[:, t] = np.nanmedian(g.close[:, lo[t] : t + 1], axis=1)
                med_tv[:, t] = np.nanmedian(g.turnover[:, lo[t] : t + 1], axis=1)
    return med_close, med_tv


def tiers(med_close: FloatArray, med_tv: FloatArray) -> npt.NDArray[np.object_]:
    """``thin`` / ``tradable`` / ``liquid`` with the study's price floor."""
    priced = np.isfinite(med_close) & (med_close >= study.MIN_PRICE)
    out = np.full(med_close.shape, "thin", dtype=object)
    out[priced & (med_tv >= study.MIN_TURNOVER)] = "tradable"
    out[priced & (med_tv >= MIN_TURNOVER_LIQUID)] = "liquid"
    out[~np.isfinite(med_tv)] = None
    return out


def spreads(g: Grid) -> tuple[FloatArray, npt.NDArray[np.int64]]:
    """Abdi–Ranaldo spread per stock and session, over pairs (t−1, t) inside the window.

    A pair counts when both sessions traded, are in the same segment, and ``t`` has no
    corporate action (the adjustment would move ``eta[t]`` against ``c[t−1]``). Returns the
    spread (NaN with fewer than ``MIN_PAIRS`` pairs) and the pair count.
    """
    n, t_n = g.ret.shape
    term = np.full((n, t_n), np.nan)
    same = np.zeros(t_n, dtype=bool)
    same[1:] = g.seg[1:] == g.seg[:-1]
    ok = np.zeros((n, t_n), dtype=bool)
    ok[:, 1:] = g.traded[:, 1:] & g.traded[:, :-1] & ~g.ca[:, 1:] & same[None, 1:]
    with np.errstate(invalid="ignore"):
        prod = (g.log_c[:, :-1] - g.eta[:, :-1]) * (g.log_c[:, :-1] - g.eta[:, 1:])
    term[:, 1:] = np.where(ok[:, 1:] & np.isfinite(prod), prod, np.nan)
    known = np.isfinite(term)
    t0 = np.where(known, term, 0.0)
    csum = np.concatenate([np.zeros((n, 1)), np.cumsum(t0, axis=1)], axis=1)
    ccnt = np.concatenate([np.zeros((n, 1), dtype=np.int64), np.cumsum(known, axis=1)], axis=1)
    # pairs end at t, so the window's first pair ends at lo + 1
    lo = _window_starts(g.seg, LIQ) + 1
    hi = np.arange(t_n) + 1
    lo = np.minimum(lo, hi)
    total = csum[:, hi] - csum[:, lo]
    cnt = ccnt[:, hi] - ccnt[:, lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        s2 = 4 * total / cnt
    spread = np.where(cnt >= MIN_PAIRS, np.sqrt(np.maximum(s2, 0.0)), np.nan)
    return spread, cnt


def costs(
    sigma: FloatArray, med_tv: FloatArray, spread: FloatArray, market: list[str]
) -> tuple[FloatArray, FloatArray, npt.NDArray[np.object_]]:
    """Round-trip cost of a ``TICKET`` order, and the spread it used with its source."""
    fallback = np.array([MARKET_SPREAD.get(m, MARKET_SPREAD["NC"]) for m in market])[:, None]
    own = np.isfinite(spread)
    used = np.where(own, spread, fallback)
    source = np.where(own, "stock", "market").astype(object)
    with np.errstate(invalid="ignore", divide="ignore"):
        impact = IMPACT_Y * sigma * np.sqrt(TICKET / med_tv)
    cost = np.where(
        np.isfinite(impact) & (med_tv > 0), used + 2 * COMMISSION + 2 * impact, MAX_COST
    )
    cost = np.where(np.isfinite(sigma), np.minimum(cost, MAX_COST), np.nan)
    return cost, used, source


@dataclass
class State:
    """Everything the state table holds, still on the grid."""

    grid: Grid
    sigma: vol.Sigma
    betas: beta_mod.Betas
    stress: FloatArray
    med_close: FloatArray
    med_tv: FloatArray
    tier: npt.NDArray[np.object_]
    spread: FloatArray
    spread_source: npt.NDArray[np.object_]
    cost: FloatArray
    signal_effect: FloatArray | None = None
    effects: pl.DataFrame | None = None  # every year's shrunk effect per kind of report


def compute(inputs: StateInputs, study_inputs: study.StudyInputs | None = None) -> State:
    """Compute the state on the grid (no lake writes).

    ``study_inputs`` (cut at the same ``as_of``) add the ``signal_effect`` column.
    """
    g = build_grid(inputs)
    sig = vol.sigma_forecasts(g.ret, g.seg, g.cal, rv=g.rv)
    t_n = len(g.cal)
    nan = np.full(t_n, np.nan)
    f = np.stack([g.index.get(name, nan) for name in FACTORS], axis=1)
    f_sig = vol.sigma_forecasts(f.T, g.seg, g.cal, vol.INDEX_VAR_FLOOR)
    betas = beta_mod.kalman_betas(g.ret, sig.h_next, f, f_sig.h_next.T, g.seg, g.cal)
    stress = regime.stress_probability(g.index.get("WIG", nan)[:, None], g.seg, g.cal)
    med_close, med_tv = liquidity(g)
    spread, _ = spreads(g)
    sigma = np.sqrt(sig.h_next)
    cost, used, source = costs(sigma, med_tv, spread, g.market)
    effect, effects = None, None
    if study_inputs is not None:
        study_cal = study_inputs.benchmark["trade_date"].to_list()
        evs = signal_effect.events_from(study.compute_event_ars(study_inputs), study_cal, g.cal)
        effect, by_year = signal_effect.daily_effect(evs, g.cal, g.isins)
        effects = signal_effect.effects_frame(by_year)
    return State(
        grid=g,
        sigma=sig,
        betas=betas,
        stress=stress,
        med_close=med_close,
        med_tv=med_tv,
        tier=tiers(med_close, med_tv),
        spread=used,
        spread_source=source,
        cost=cost,
        signal_effect=effect,
        effects=effects,
    )


def to_frame(s: State) -> pl.DataFrame:
    """One row per (stock, session) on which the stock has a price row."""
    g = s.grid
    rows, cols = np.nonzero(g.has_row)
    sigma = np.sqrt(s.sigma.h_next)
    sigma_20 = np.sqrt(s.sigma.horizon_var(HORIZON))
    with np.errstate(invalid="ignore", divide="ignore"):
        surprise = s.betas.resid / np.sqrt(s.betas.resid_var)
    src = np.vectorize(vol.SOURCE_NAMES.get, otypes=[object])(s.sigma.source)

    def f(a: FloatArray) -> list[float | None]:
        v = a[rows, cols]
        return [float(x) if math.isfinite(x) else None for x in v]

    df = pl.DataFrame(
        {
            "isin": [g.isins[i] for i in rows],
            "trade_date": [g.cal[j] for j in cols],
            "market": [g.market[i] for i in rows],
            "known_at": [g.known_at[j] for j in cols],
            "sigma": f(sigma),
            "sigma_20": f(sigma_20),
            "sigma_source": src[rows, cols].tolist(),
            "beta_wig": f(s.betas.beta[:, :, 0]),
            "beta_tbsp": f(s.betas.beta[:, :, 1]),
            "resid": f(s.betas.resid),
            "surprise": f(surprise),
            "regime_stress": [
                float(s.stress[j]) if math.isfinite(s.stress[j]) else None for j in cols
            ],
            "median_close": f(s.med_close),
            "median_turnover": f(s.med_tv),
            "liquidity_tier": s.tier[rows, cols].tolist(),
            "spread": f(s.spread),
            "spread_source": [
                x if math.isfinite(sigma[i, j]) else None
                for x, i, j in zip(s.spread_source[rows, cols].tolist(), rows, cols, strict=True)
            ],
            "cost": f(s.cost),
            "signal_effect": f(s.signal_effect)
            if s.signal_effect is not None
            else [None] * len(rows),
        },
        schema_overrides={
            "known_at": pl.Datetime("us", "UTC"),
            "sigma_source": pl.String,
            "liquidity_tier": pl.String,
            "spread_source": pl.String,
            "signal_effect": pl.Float64,
        },
    )
    return df.sort(["isin", "trade_date"])


def run_market_state(duckdb_file: Path, work: Path, day: date) -> Path:
    """Compute and write ``{work}/gold/market_state.parquet``."""
    import warnings

    as_of = study.as_of_for(day)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        state = compute(load_inputs(duckdb_file, as_of), study.load_inputs(duckdb_file, as_of))
    df = to_frame(state)
    out = work / "gold" / "market_state.parquet"
    lake.write_frame(df, out)
    if state.effects is not None:
        lake.write_frame(state.effects, work / "gold" / "signal_effects.parquet")
    log.info(
        "market_state: %d rows, %d stocks, q=%s, sha256=%s",
        df.height,
        len(state.grid.isins),
        state.betas.q,
        study.sha256_file(out),
    )
    return out
