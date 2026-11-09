"""Event studies, bootstrap CIs, Benjamini-Hochberg FDR, walk-forward and deflated Sharpe.

Method (details and caveats in ``docs/METHODOLOGY.md``):

* **Day 0** is the first session whose close (17:05 Warsaw) is after ``known_at``.
* **Market model** ``r_i = a + b·r_WIG`` estimated by OLS on sessions [−250, −30]
  (at least ``MIN_EST_OBS`` valid returns). Abnormal return ``AR_t = r_t − (a + b·r_WIG,t)``.
* **Entry** is the close of the first session after the evening digest that carries the
  report: a report published at 10:00 is in that evening's digest, so the first close a
  reader can trade is the next session's. Statistics and the walk-forward measure abnormal
  returns from that close: windows (entry, entry+h] for h in 1/5/20/60 sessions. The
  [−5,+20] chart path stays relative to day 0, so the reaction before entry is visible.
* **Uncertainty:** 95 % percentile bootstrap over events, fixed seed per signal/horizon.
* **Multiple testing:** BH q-values across every (signal, horizon).
* **Walk-forward:** expanding yearly windows. Before each year the rule (direction and
  holding period) is chosen from earlier events only; trades enter at the entry close and
  exit h sessions later, net of 0.3 % round-trip costs.
* **Point in time:** every input row must have ``known_at <= as_of``; post-event returns
  that weren't known by ``as_of`` make the event ineligible for that horizon.

Everything is deterministic: sorted inputs, seeded RNGs, no wall-clock values in outputs.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy import stats as sps

from undercurrent import lake

log = logging.getLogger(__name__)

HORIZONS: tuple[int, ...] = (1, 5, 20, 60)
PATH_WINDOW = (-5, 20)
EST_WINDOW = (-250, -30)
MIN_EST_OBS = 120
BOOTSTRAP_DRAWS = 2000
ROUND_TRIP_COST = 0.003
MIN_TRAIN_EVENTS = 30
MIN_EVENTS_FOR_STATS = 10
# Standard errors and the bootstrap treat each stock as one cluster: repeated notifications
# on the same stock (daily insider filings, say) are not independent observations.
MIN_CLUSTERS = 5
# The walk-forward result counts only with at least this many out-of-sample trades.
MIN_OOS_TRADES = 10
SEED = 20261109
SIGNIFICANCE = 0.05
WARSAW = ZoneInfo("Europe/Warsaw")
# The nightly digest is out by this Warsaw time (the freshness SLO). Reports published
# later reach readers in the next digest.
DIGEST_LOCAL = time(20, 30)
# Events whose entry would be more than this many sessions after day 0 are not traded.
MAX_ENTRY_LAG = 5

FloatArray = npt.NDArray[np.float64]


class LookAheadError(AssertionError):
    """An input row was known after the as-of date."""


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class StudyInputs:
    """Point-in-time inputs: events, per-ISIN adjusted returns and WIG returns."""

    as_of: datetime
    events: pl.DataFrame  # report_id, signal_key, isin, known_at, event_date
    returns: pl.DataFrame  # isin, trade_date, adj_ret, known_at
    benchmark: pl.DataFrame  # trade_date, wig_ret, known_at


def signal_key(signal_type: str, subtype: str | None, insider_side: str | None) -> str:
    """Grouping key for statistics, e.g. ``inside_info:profit_warning``, ``insider_tx:buy``."""
    if signal_type == "insider_tx":
        return f"insider_tx:{insider_side if insider_side in ('buy', 'sell') else 'unknown'}"
    return f"{signal_type}:{subtype}" if subtype else signal_type


def load_inputs(duckdb_file: Path, as_of: datetime) -> StudyInputs:
    """Read gold tables from DuckDB and cut them at ``as_of`` by ``known_at``."""
    import duckdb

    con = duckdb.connect(str(duckdb_file))
    try:
        ev = con.sql(
            "select report_id, signal_type, subtype, insider_side, isin, known_at, event_date "
            "from events where isin is not null and event_date is not null"
        ).pl()
        rets = con.sql(
            "select isin, trade_date, adj_ret, known_at from adjusted_prices "
            "where adj_ret is not null"
        ).pl()
        bench = con.sql(
            "select b.trade_date, b.wig_ret, s.known_at from benchmark_returns b "
            "join benchmark s using (trade_date) where b.wig_ret is not null"
        ).pl()
    finally:
        con.close()
    return cut_inputs(ev, rets, bench, as_of)


def cut_inputs(
    events: pl.DataFrame, returns: pl.DataFrame, benchmark: pl.DataFrame, as_of: datetime
) -> StudyInputs:
    """Apply the point-in-time filter and build signal keys."""
    ts = pl.lit(as_of).cast(pl.Datetime("us", "UTC"))

    def cut(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(pl.col("known_at").cast(pl.Datetime("us", "UTC"))).filter(
            pl.col("known_at") <= ts
        )

    ev = cut(events).with_columns(
        pl.struct("signal_type", "subtype", "insider_side")
        .map_elements(
            lambda s: signal_key(s["signal_type"], s["subtype"], s["insider_side"]),
            return_dtype=pl.String,
        )
        .alias("signal_key")
    )
    ev = ev.select("report_id", "signal_key", "isin", "known_at", "event_date").sort("report_id")
    return StudyInputs(
        as_of=as_of,
        events=ev,
        returns=cut(returns).sort(["isin", "trade_date"]),
        benchmark=cut(benchmark).sort("trade_date"),
    )


def assert_point_in_time(inputs: StudyInputs) -> None:
    """Raise :class:`LookAheadError` if any input row is known after ``as_of``."""
    ts = inputs.as_of
    for name, df in (
        ("events", inputs.events),
        ("returns", inputs.returns),
        ("benchmark", inputs.benchmark),
    ):
        if df.height and df["known_at"].max() > ts:  # type: ignore[operator]
            raise LookAheadError(f"{name} has rows known after {ts.isoformat()}")


# --------------------------------------------------------------------------- per-event ARs


@dataclass(frozen=True)
class EventAR:
    """Abnormal returns of one event over the path window, NaN where unknown."""

    report_id: str
    signal_key: str
    isin: str
    event_date: date
    ar: FloatArray  # index 0 ↔ relative day PATH_WINDOW[0]; extended past max(HORIZONS)
    entry: int = 1  # entry session relative to day 0; -1 if not yet known
    day0_idx: int = -1  # position of day 0 in the session calendar


def _offset_range() -> range:
    return range(PATH_WINDOW[0], max(max(HORIZONS) + MAX_ENTRY_LAG, PATH_WINDOW[1]) + 1)


def digest_day(known_at: datetime, sessions: list[date]) -> date | None:
    """Session whose evening digest first carries a report known at ``known_at``.

    The nightly run publishes after each session; it covers everything published since the
    previous run, so weekend and holiday reports land in the next session's digest.
    """
    local = known_at.astimezone(WARSAW)
    d = local.date() + (timedelta(days=1) if local.time() > DIGEST_LOCAL else timedelta(0))
    i = bisect.bisect_left(sessions, d)
    return sessions[i] if i < len(sessions) else None


def entry_offset(known_at: datetime, event_date: date, sessions: list[date]) -> int:
    """Entry session relative to day 0: the first session after the digest. -1 if unknown.

    ``sessions`` must be sorted.
    """
    dig = digest_day(known_at, sessions)
    if dig is None:
        return -1
    entry = bisect.bisect_right(sessions, dig)
    day0 = bisect.bisect_left(sessions, event_date)
    if entry >= len(sessions) or day0 >= len(sessions) or sessions[day0] != event_date:
        return -1
    return entry - day0


def compute_event_ars(inputs: StudyInputs) -> list[EventAR]:
    """Market-model abnormal returns for every eligible event."""
    cal = inputs.benchmark["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(cal)}
    mkt = inputs.benchmark["wig_ret"].to_numpy().astype(np.float64)
    n_cal = len(cal)
    by_isin: dict[str, FloatArray] = {}
    for isin, grp in inputs.returns.group_by("isin", maintain_order=True):
        arr = np.full(n_cal, np.nan)
        for d, r in zip(grp["trade_date"].to_list(), grp["adj_ret"].to_list(), strict=True):
            i = idx.get(d)
            if i is not None and r is not None and math.isfinite(r):
                arr[i] = r
        by_isin[str(isin[0])] = arr

    offsets = np.array(list(_offset_range()))
    out: list[EventAR] = []
    for row in inputs.events.iter_rows(named=True):
        e = idx.get(row["event_date"])
        ri = by_isin.get(row["isin"])
        if e is None or ri is None:
            continue
        lo, hi = e + EST_WINDOW[0], e + EST_WINDOW[1]
        if lo < 0:
            continue
        y, x = ri[lo : hi + 1], mkt[lo : hi + 1]
        ok = np.isfinite(y) & np.isfinite(x)
        if ok.sum() < MIN_EST_OBS:
            continue
        b, a = np.polyfit(x[ok], y[ok], 1)
        pos = e + offsets
        ar = np.full(len(offsets), np.nan)
        valid = (pos >= 0) & (pos < n_cal)
        p = pos[valid]
        ar[valid] = ri[p] - (a + b * mkt[p])
        entry = entry_offset(row["known_at"], row["event_date"], cal)
        out.append(
            EventAR(
                row["report_id"], row["signal_key"], row["isin"], row["event_date"], ar, entry, e
            )
        )
    return out


def _car(ar: FloatArray, start: int, end: int) -> float:
    """Sum of ARs over relative days [start, end]; NaN if any day is unknown."""
    i0, i1 = start - PATH_WINDOW[0], end - PATH_WINDOW[0]
    seg = ar[i0 : i1 + 1]
    return float(seg.sum()) if np.all(np.isfinite(seg)) else math.nan


def trade_car(e: EventAR, horizon: int) -> float:
    """Abnormal return from the entry close to ``horizon`` sessions later; NaN if unknown."""
    if not 0 <= e.entry <= MAX_ENTRY_LAG:
        return math.nan
    return _car(e.ar, e.entry + 1, e.entry + horizon)


# --------------------------------------------------------------------------- statistics


def _seed(*parts: object) -> int:
    h = hashlib.sha256("|".join(map(str, (SEED, *parts))).encode()).digest()
    return int.from_bytes(h[:8], "big")


def bootstrap_ci(values: FloatArray, key: str, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean, resampling events, seeded by ``key``."""
    rng = np.random.default_rng(_seed(key))
    n = len(values)
    draws = rng.integers(0, n, size=(BOOTSTRAP_DRAWS, n))
    means = values[draws].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def bootstrap_ci_clustered(
    values: FloatArray, groups: list[str], key: str, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile bootstrap CI of the pooled mean, resampling whole clusters (stocks)."""
    rng = np.random.default_rng(_seed(key))
    names = sorted(set(groups))
    sums = np.array([values[[g == n for g in groups]].sum() for n in names])
    counts = np.array([sum(1 for g in groups if g == n) for n in names], dtype=np.float64)
    draws = rng.integers(0, len(names), size=(BOOTSTRAP_DRAWS, len(names)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def cluster_t(values: FloatArray, groups: list[str]) -> tuple[float, float, int]:
    """t-statistic of the mean with cluster-robust (CR1) standard error; (t, p, clusters).

    ``se² = G/(G−1) · Σ_g (Σ_{i∈g} (x_i − x̄))² / n²`` with ``G − 1`` degrees of freedom. With
    one observation per cluster this equals the usual standard error of the mean.
    """
    n = len(values)
    names = sorted(set(groups))
    g = len(names)
    if g < 2:
        return math.nan, math.nan, g
    dev = values - values.mean()
    score = np.array([dev[[x == nm for x in groups]].sum() for nm in names])
    se = math.sqrt(g / (g - 1) * float((score**2).sum())) / n
    if se == 0:
        return math.nan, math.nan, g
    t = float(values.mean() / se)
    return t, float(2 * sps.t.sf(abs(t), df=g - 1)), g


def benjamini_hochberg(p: FloatArray) -> FloatArray:
    """BH-adjusted q-values (NaNs stay NaN)."""
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    m = len(pv)
    if m == 0:
        return q
    order = np.argsort(pv, kind="stable")
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(m)
    res[order] = np.minimum(ranked, 1.0)
    q[ok] = res
    return q


def deflated_sharpe(returns: FloatArray, n_trials: int, sr_trials_var: float) -> float:
    """Deflated Sharpe ratio of per-trade returns.

    ``sr_trials_var`` is the cross-sectional variance of the Sharpe ratios of all trials.
    Returns a probability in [0, 1]; NaN if there are too few observations.
    """
    t = len(returns)
    if t < 3 or np.std(returns) == 0:
        return math.nan
    sr = float(np.mean(returns) / np.std(returns, ddof=1))
    g3 = float(sps.skew(returns))
    g4 = float(sps.kurtosis(returns, fisher=False))
    emc = 0.5772156649
    n = max(n_trials, 2)
    sr0 = math.sqrt(max(sr_trials_var, 0.0)) * (
        (1 - emc) * sps.norm.ppf(1 - 1 / n) + emc * sps.norm.ppf(1 - 1 / (n * math.e))
    )
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    if denom <= 0:
        return math.nan
    return float(sps.norm.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)))


# --------------------------------------------------------------------------- walk-forward


@dataclass(frozen=True)
class Rule:
    """A tradable rule: direction and holding period, picked from past events."""

    direction: int  # +1 long, −1 short
    horizon: int


def exit_index(e: EventAR, horizon: int) -> int:
    """Calendar position of the exit session of a ``horizon``-session trade, or -1."""
    if e.day0_idx < 0 or not 0 <= e.entry <= MAX_ENTRY_LAG:
        return -1
    return e.day0_idx + e.entry + horizon


def _trade_returns(
    evs: list[EventAR], horizon: int, known_before: int | None = None
) -> tuple[FloatArray, list[date]]:
    """Abnormal return from the entry close to ``horizon`` sessions later, per event.

    With ``known_before``, only trades whose exit session lies before that calendar
    position count, so a rule is never chosen with returns that were still in the future.
    """
    vals, dates = [], []
    for e in evs:
        if known_before is not None and not 0 <= exit_index(e, horizon) < known_before:
            continue
        v = trade_car(e, horizon)
        if math.isfinite(v):
            vals.append(v)
            dates.append(e.event_date)
    return np.array(vals, dtype=np.float64), dates


def choose_rule(train: list[EventAR], known_before: int | None = None) -> Rule | None:
    """Pick the (direction, horizon) with the best t-statistic of net returns on ``train``.

    Ranking by raw mean would favour the noisiest (longest) horizon by chance, so we rank by
    ``mean_net / standard_error``. If no candidate has a positive past net mean, return
    ``None`` (don't trade that year).
    """
    best: tuple[float, Rule] | None = None
    for h in HORIZONS:
        r, _ = _trade_returns(train, h, known_before)
        if len(r) < MIN_TRAIN_EVENTS:
            continue
        se = float(np.std(r, ddof=1)) / math.sqrt(len(r))
        for d in (1, -1):
            net = float(np.mean(d * r)) - ROUND_TRIP_COST
            if net <= 0 or se == 0:
                continue
            t = net / se
            if best is None or t > best[0]:
                best = (t, Rule(d, h))
    return best[1] if best else None


def walk_forward(
    evs: list[EventAR], calendar: list[date] | None = None
) -> tuple[FloatArray, list[Rule]]:
    """Expanding yearly walk-forward. Returns out-of-sample net trade returns and rules used.

    The rule for year Y is chosen from trades that had *exited* before Y began (a December
    event's 60-session trade is still open in January, so it can't inform January's rule).
    """
    years = sorted({e.event_date.year for e in evs})
    oos: list[float] = []
    rules: list[Rule] = []
    for y in years:
        train = [e for e in evs if e.event_date.year < y]
        test = [e for e in evs if e.event_date.year == y]
        cutoff = bisect.bisect_left(calendar, date(y, 1, 1)) if calendar is not None else None
        rule = choose_rule(train, cutoff)
        if rule is None:
            continue
        r, _ = _trade_returns(test, rule.horizon)
        oos.extend((rule.direction * r - ROUND_TRIP_COST).tolist())
        rules.append(rule)
    return np.array(oos, dtype=np.float64), rules


# --------------------------------------------------------------------------- signal_stats

STATS_SCHEMA: dict[str, Any] = {
    "as_of": pl.Datetime("us", "UTC"),
    "signal_key": pl.String,
    "horizon": pl.Int64,
    "n": pl.Int64,
    "mean_car": pl.Float64,
    "median_car": pl.Float64,
    "hit_rate": pl.Float64,
    "ci_lo": pl.Float64,
    "ci_hi": pl.Float64,
    "t_stat": pl.Float64,
    "p_value": pl.Float64,
    "q_value": pl.Float64,
    "oos_n": pl.Int64,
    "oos_mean_net": pl.Float64,
    "oos_last_rule": pl.String,
    "deflated_sharpe": pl.Float64,
    "verdict": pl.String,
}


def compute_signal_stats(inputs: StudyInputs) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (signal_stats, car_paths). Pure function of ``inputs``."""
    assert_point_in_time(inputs)
    ars = dedupe_events(compute_event_ars(inputs))
    calendar = inputs.benchmark["trade_date"].to_list()
    by_key: dict[str, list[EventAR]] = {}
    for e in ars:
        by_key.setdefault(e.signal_key, []).append(e)

    rows: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    wf: dict[str, tuple[FloatArray, list[Rule]]] = {}
    for key in sorted(by_key):
        evs = sorted(by_key[key], key=lambda e: (e.event_date, e.report_id))
        wf[key] = walk_forward(evs, calendar)
        for h in HORIZONS:
            pairs = [(trade_car(e, h), e.isin) for e in evs]
            pairs = [(v, g) for v, g in pairs if math.isfinite(v)]
            cars = np.array([v for v, _ in pairs], dtype=np.float64)
            groups = [g for _, g in pairs]
            n = len(cars)
            row: dict[str, Any] = {"as_of": inputs.as_of, "signal_key": key, "horizon": h, "n": n}
            if n >= MIN_EVENTS_FOR_STATS and len(set(groups)) >= MIN_CLUSTERS:
                t, p_val, _ = cluster_t(cars, groups)
                lo, hi = bootstrap_ci_clustered(cars, groups, f"{key}|{h}")
                row |= {
                    "mean_car": float(np.mean(cars)),
                    "median_car": float(np.median(cars)),
                    "hit_rate": float(np.mean(cars > 0)),
                    "ci_lo": lo,
                    "ci_hi": hi,
                    "t_stat": t,
                    "p_value": p_val,
                }
            rows.append(row)
        # average [-5, +20] CAR path with a bootstrap band
        lo_d, hi_d = PATH_WINDOW
        mat = np.array([np.cumsum(e.ar[: hi_d - lo_d + 1]) for e in evs])
        mat = mat[np.all(np.isfinite(mat), axis=1)] if len(mat) else mat
        if len(mat) >= MIN_EVENTS_FOR_STATS:
            rng = np.random.default_rng(_seed(key, "path"))
            draws = rng.integers(0, len(mat), size=(BOOTSTRAP_DRAWS, len(mat)))
            boot = mat[draws].mean(axis=1)
            band_lo, band_hi = np.quantile(boot, [0.025, 0.975], axis=0)
            for i, d in enumerate(range(lo_d, hi_d + 1)):
                paths.append(
                    {
                        "as_of": inputs.as_of,
                        "signal_key": key,
                        "day": d,
                        "mean_car": float(mat[:, i].mean()),
                        "lo": float(band_lo[i]),
                        "hi": float(band_hi[i]),
                        "n": len(mat),
                    }
                )

    df = pl.DataFrame(
        rows,
        schema={
            k: v
            for k, v in STATS_SCHEMA.items()
            if k
            in {
                "as_of",
                "signal_key",
                "horizon",
                "n",
                "mean_car",
                "median_car",
                "hit_rate",
                "ci_lo",
                "ci_hi",
                "t_stat",
                "p_value",
            }
        },
    )
    q = benjamini_hochberg(df["p_value"].fill_null(np.nan).to_numpy().astype(np.float64))
    df = df.with_columns(pl.Series("q_value", q).fill_nan(None))

    # deflated Sharpe: one trial per (key, horizon, direction)
    srs = []
    for oos, _ in wf.values():
        if len(oos) >= 3 and np.std(oos) > 0:
            srs.append(float(np.mean(oos) / np.std(oos, ddof=1)))
    sr_var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    n_trials = max(1, len(by_key) * len(HORIZONS) * 2)

    extra = []
    for key in sorted(by_key):
        oos, rules = wf[key]
        last = rules[-1] if rules else None
        dsr = deflated_sharpe(oos, n_trials, sr_var)
        extra.append(
            {
                "signal_key": key,
                "oos_n": len(oos),
                "oos_mean_net": float(np.mean(oos)) if len(oos) else None,
                "oos_last_rule": (
                    f"{'long' if last.direction > 0 else 'short'} {last.horizon}d" if last else None
                ),
                "deflated_sharpe": None if math.isnan(dsr) else dsr,
            }
        )
    ex = pl.DataFrame(
        extra,
        schema={
            "signal_key": pl.String,
            "oos_n": pl.Int64,
            "oos_mean_net": pl.Float64,
            "oos_last_rule": pl.String,
            "deflated_sharpe": pl.Float64,
        },
    )
    df = df.join(ex, on="signal_key", how="left")
    best = df.group_by("signal_key").agg(
        pl.col("q_value").min().alias("best_q"),
        pl.col("mean_car").is_not_null().any().alias("has_stats"),
    )
    significant = pl.col("best_q") < SIGNIFICANCE
    tested = pl.col("oos_n").fill_null(0) >= MIN_OOS_TRADES
    df = (
        df.join(best, on="signal_key", how="left")
        .with_columns(
            pl.when(~pl.col("has_stats"))
            .then(pl.lit("too_few"))
            .when(significant & ~tested)
            .then(pl.lit("untested"))
            .when(significant & (pl.col("oos_mean_net") > 0))
            .then(pl.lit("holds_up"))
            .when(significant)
            .then(pl.lit("eaten_by_costs"))
            .otherwise(pl.lit("noise"))
            .alias("verdict")
        )
        .drop("best_q", "has_stats")
    )
    df = df.select(list(STATS_SCHEMA)).cast(STATS_SCHEMA).sort(["signal_key", "horizon"])  # type: ignore[arg-type]
    path_df = pl.DataFrame(
        paths,
        schema={
            "as_of": pl.Datetime("us", "UTC"),
            "signal_key": pl.String,
            "day": pl.Int64,
            "mean_car": pl.Float64,
            "lo": pl.Float64,
            "hi": pl.Float64,
            "n": pl.Int64,
        },
    )
    return df, path_df.sort(["signal_key", "day"])


def dedupe_events(ars: list[EventAR]) -> list[EventAR]:
    """One event per (signal, stock, day 0).

    Several reports about the same news (or a report and its correction) would otherwise
    count the same price move several times.
    """
    seen: set[tuple[str, str, date]] = set()
    out = []
    for e in sorted(ars, key=lambda e: (e.signal_key, e.isin, e.event_date, e.report_id)):
        k = (e.signal_key, e.isin, e.event_date)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def event_cars(inputs: StudyInputs) -> pl.DataFrame:
    """Per-event CARs (for the event detail page and "closest past events")."""
    out = []
    for e in compute_event_ars(inputs):
        out.append(
            {
                "report_id": e.report_id,
                "signal_key": e.signal_key,
                "isin": e.isin,
                "event_date": e.event_date,
                **{f"car_{h}": trade_car(e, h) for h in HORIZONS},
                "path": [float(x) for x in np.cumsum(e.ar[: PATH_WINDOW[1] - PATH_WINDOW[0] + 1])],
            }
        )
    df = pl.DataFrame(
        out,
        schema={
            "report_id": pl.String,
            "signal_key": pl.String,
            "isin": pl.String,
            "event_date": pl.Date,
            **{f"car_{h}": pl.Float64 for h in HORIZONS},
            "path": pl.List(pl.Float64),
        },
    )
    return df.with_columns(pl.col(f"car_{h}").fill_nan(None) for h in HORIZONS).sort("report_id")


def as_of_for(day: date) -> datetime:
    """As-of timestamp of a nightly run: 23:59:59 Warsaw on ``day``, in UTC."""
    from zoneinfo import ZoneInfo

    return datetime(
        day.year, day.month, day.day, 23, 59, 59, tzinfo=ZoneInfo("Europe/Warsaw")
    ).astimezone(UTC)


def sha256_file(path: Path) -> str:
    """SHA-256 of a file (reproducibility check)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_study(duckdb_file: Path, work: Path, day: date) -> Path:
    """Compute and write ``{work}/gold/signal_stats.parquet`` (+ CAR paths, event CARs)."""
    inputs = load_inputs(duckdb_file, as_of_for(day))
    stats, paths = compute_signal_stats(inputs)
    out = work / "gold" / "signal_stats.parquet"
    lake.write_frame(stats, out)
    lake.write_frame(paths, work / "gold" / "car_paths.parquet")
    lake.write_frame(event_cars(inputs), work / "gold" / "event_cars.parquet")
    log.info("signal_stats: %d rows, sha256=%s", stats.height, sha256_file(out))
    return out
