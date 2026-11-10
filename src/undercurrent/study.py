"""Event studies, bootstrap CIs, Benjamini-Hochberg FDR, walk-forward and deflated Sharpe.

Method (details and caveats in ``docs/METHODOLOGY.md``):

* **Day 0** is the first session whose close (17:05 Warsaw) is after ``known_at``.
* **Excess return** is the stock's compounded return minus WIG's over the same sessions
  (buy-and-hold, market-adjusted). No fitted baseline: a fitted intercept absorbs the run-up
  that often precedes news and then shows up as a spurious negative drift afterwards.
* **Entry** is the close of the first session after the evening digest that carries the
  report: a report published at 10:00 is in that evening's digest, so the first close a
  reader can trade is the next session's. Statistics and the walk-forward measure abnormal
  returns from that close: windows (entry, entry+h] for h in 1/5/20/60 sessions. The
  [−5,+20] chart path stays relative to day 0, so the reaction before entry is visible.
* **Uncertainty:** cluster-robust t-test and 95 % percentile bootstrap, both clustered two
  ways, by stock and by calendar month; fixed seed per signal/horizon.
* **Multiple testing:** BH q-values across every (signal, horizon).
* **Walk-forward:** expanding yearly windows. Before each year the rule (direction and
  holding period) is chosen from earlier events only; trades enter at the entry close and
  exit h sessions later, net of round-trip costs (2.0 % on GPW, 5.2 % on NewConnect).
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
# A stock needs a recent price history, and must actually trade, before its events count:
# of the LIQ_WINDOW sessions before day 0 at least MIN_HISTORY must have a return, and the
# stock must have traded (volume > 0) on at least MIN_TRADED_SHARE of those.
LIQ_WINDOW = 60
MIN_HISTORY = 40
MIN_TRADED_SHARE = 0.5
BOOTSTRAP_DRAWS = 2000
# Round-trip trading cost: one effective bid-ask spread plus 0.2 % commission each way.
# Spreads are estimated from daily high/low/close (scripts/estimate_spreads.py), weighted by
# where the events are: about 1.6 % on GPW and 4.8 % on NewConnect. Most events are in small
# caps; a blue chip costs far less than this.
ROUND_TRIP_COST = 0.020
ROUND_TRIP_COST_NC = 0.052
COST_BY_MARKET = {"GPW": ROUND_TRIP_COST, "NC": ROUND_TRIP_COST_NC}
MIN_TRAIN_EVENTS = 30
MIN_EVENTS_FOR_STATS = 10
# Standard errors and the bootstrap cluster two ways. By stock: repeated notifications on
# one stock (daily insider filings, say) are not independent. By calendar month of day 0:
# events in the same weeks share whatever moved small caps then, and their windows overlap.
MIN_CLUSTERS = 5
# The walk-forward result counts only with at least this many out-of-sample trades.
MIN_OOS_TRADES = 10
SEED = 20261109
SIGNIFICANCE = 0.05
WARSAW = ZoneInfo("Europe/Warsaw")
# Editorial cut-off of the nightly digest, Warsaw time. It lies before the earliest scheduled
# run (17:47 UTC = 18:47 in winter, 19:47 in summer), so every run has seen everything up to
# it. A report published later goes into the next session's digest. The site uses the same
# cut-off (``digest_window``), so the study and what readers saw cannot drift apart.
DIGEST_CUTOFF_LOCAL = time(18, 30)
# Events whose entry would be more than this many sessions after day 0 are not traded.
MAX_ENTRY_LAG = 5
# Real sessions are never more than a week apart. A longer step in the calendar is a hole in
# the data (months not backfilled yet): no window may reach across it.
MAX_SESSION_GAP_DAYS = 7

FloatArray = npt.NDArray[np.float64]


class LookAheadError(AssertionError):
    """An input row was known after the as-of date."""


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class StudyInputs:
    """Point-in-time inputs: events, per-ISIN adjusted returns and WIG returns."""

    as_of: datetime
    events: pl.DataFrame  # report_id, signal_key, isin, known_at, event_date, market
    returns: pl.DataFrame  # isin, trade_date, adj_ret, known_at[, volume]
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
            "select report_id, signal_type, subtype, insider_side, isin, known_at, event_date, "
            "market from events where isin is not null and event_date is not null"
        ).pl()
        rets = con.sql(
            "select isin, trade_date, adj_ret, volume, known_at from adjusted_prices "
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
    if "market" not in ev.columns:
        ev = ev.with_columns(pl.lit(None, dtype=pl.String).alias("market"))
    ev = ev.select("report_id", "signal_key", "isin", "known_at", "event_date", "market").sort(
        "report_id"
    )
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
    """Daily returns of one event around day 0, NaN where unknown.

    Arrays are indexed by relative day: index 0 is ``PATH_WINDOW[0]``, and they run past
    ``max(HORIZONS)`` so every trade window fits.
    """

    report_id: str
    signal_key: str
    isin: str
    event_date: date
    ar: FloatArray  # daily excess return: stock minus WIG
    entry: int = 1  # entry session relative to day 0; -1 if not yet known
    day0_idx: int = -1  # position of day 0 in the session calendar
    cost: float = ROUND_TRIP_COST  # round-trip trading cost on this stock's market
    ret: FloatArray | None = None  # the stock's daily returns
    mkt: FloatArray | None = None  # WIG's daily returns
    tradable: bool = True  # the stock traded on the entry session

    @property
    def month(self) -> str:
        """Calendar month of day 0: the time cluster."""
        return f"{self.event_date:%Y-%m}"


def _offset_range() -> range:
    return range(PATH_WINDOW[0], max(max(HORIZONS) + MAX_ENTRY_LAG, PATH_WINDOW[1]) + 1)


def digest_day(known_at: datetime, sessions: list[date]) -> date | None:
    """Session whose evening digest first carries a report known at ``known_at``.

    Each session's digest covers everything published after the previous session's cut-off
    and up to its own, so evening, weekend and holiday reports land in the next session's
    digest.
    """
    local = known_at.astimezone(WARSAW)
    late = local.time() > DIGEST_CUTOFF_LOCAL
    d = local.date() + (timedelta(days=1) if late else timedelta(0))
    i = bisect.bisect_left(sessions, d)
    return sessions[i] if i < len(sessions) else None


def digest_window(day: date, sessions: list[date]) -> tuple[datetime, datetime]:
    """Publication window ``(start, end]`` of the digest for ``day``, as UTC timestamps.

    It opens at the cut-off of the last session before ``day`` and closes at the cut-off on
    ``day``. On a day without a session the same window is shown, and the next session's
    digest covers it again. ``sessions`` must be sorted; with no earlier session known, the
    previous weekday is used.
    """
    i = bisect.bisect_left(sessions, day)
    if i > 0:
        prev = sessions[i - 1]
    else:
        prev = day - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)

    def cut(d: date) -> datetime:
        return datetime.combine(d, DIGEST_CUTOFF_LOCAL, tzinfo=WARSAW).astimezone(UTC)

    return cut(prev), cut(day)


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


def calendar_segments(cal: list[date]) -> npt.NDArray[np.int64]:
    """Segment number of each session: it goes up by one after every hole in the calendar."""
    seg = np.zeros(len(cal), dtype=np.int64)
    for i in range(1, len(cal)):
        seg[i] = seg[i - 1] + ((cal[i] - cal[i - 1]).days > MAX_SESSION_GAP_DAYS)
    return seg


def compute_event_ars(inputs: StudyInputs) -> list[EventAR]:
    """Daily stock and WIG returns around day 0 for every eligible event.

    Everything stays inside the event's own calendar segment: sessions on the far side of a
    data hole are not its neighbours. Stocks without a recent history, or that mostly do not
    trade, are left out (see ``LIQ_WINDOW``).
    """
    cal = inputs.benchmark["trade_date"].to_list()
    idx = {d: i for i, d in enumerate(cal)}
    seg = calendar_segments(cal)
    mkt = inputs.benchmark["wig_ret"].to_numpy().astype(np.float64)
    n_cal = len(cal)
    has_volume = "volume" in inputs.returns.columns
    by_isin: dict[str, FloatArray] = {}
    traded_by_isin: dict[str, npt.NDArray[np.bool_]] = {}
    for isin, grp in inputs.returns.group_by("isin", maintain_order=True):
        arr = np.full(n_cal, np.nan)
        traded = np.zeros(n_cal, dtype=bool)
        vols = grp["volume"].to_list() if has_volume else [1] * grp.height
        for d, r, v in zip(
            grp["trade_date"].to_list(), grp["adj_ret"].to_list(), vols, strict=True
        ):
            i = idx.get(d)
            if i is not None and r is not None and math.isfinite(r):
                arr[i] = r
                traded[i] = bool(v and v > 0)
        by_isin[str(isin[0])] = arr
        traded_by_isin[str(isin[0])] = traded

    offsets = np.array(list(_offset_range()))
    out: list[EventAR] = []
    for row in inputs.events.iter_rows(named=True):
        e = idx.get(row["event_date"])
        ri = by_isin.get(row["isin"])
        if e is None or ri is None:
            continue
        # day 0 more than a week after publication: the sessions in between are missing
        published = row["known_at"].astimezone(WARSAW).date()
        if (row["event_date"] - published).days > MAX_SESSION_GAP_DAYS:
            continue
        traded = traded_by_isin[row["isin"]]
        hist = slice(max(0, e - LIQ_WINDOW), e)
        known = np.isfinite(ri[hist]) & (seg[hist] == seg[e])
        if known.sum() < MIN_HISTORY or traded[hist][known].mean() < MIN_TRADED_SHARE:
            continue
        pos = e + offsets
        valid = (pos >= 0) & (pos < n_cal)
        valid[valid] = seg[pos[valid]] == seg[e]
        p = pos[valid]
        ret = np.full(len(offsets), np.nan)
        mk = np.full(len(offsets), np.nan)
        ret[valid] = ri[p]
        mk[valid] = mkt[p]
        entry = entry_offset(row["known_at"], row["event_date"], cal)
        tradable = True
        if 0 <= entry <= MAX_ENTRY_LAG and e + entry < n_cal:
            tradable = bool(traded[e + entry])
        out.append(
            EventAR(
                row["report_id"],
                row["signal_key"],
                row["isin"],
                row["event_date"],
                ret - mk,
                entry,
                e,
                COST_BY_MARKET.get(row["market"], ROUND_TRIP_COST),
                ret,
                mk,
                tradable,
            )
        )
    return out


def _excess(e: EventAR, start: int, end: int) -> float:
    """Compounded stock return minus compounded WIG return over relative days [start, end].

    NaN if any day is unknown. This is what holding the stock against the index earns; unlike
    a sum of daily differences it does not accumulate bid-ask bounce.
    """
    i0, i1 = start - PATH_WINDOW[0], end - PATH_WINDOW[0]
    if e.ret is None or e.mkt is None:  # only daily excess returns given: add them up
        seg = e.ar[i0 : i1 + 1]
        return float(seg.sum()) if np.all(np.isfinite(seg)) else math.nan
    r, m = e.ret[i0 : i1 + 1], e.mkt[i0 : i1 + 1]
    if not (np.all(np.isfinite(r)) and np.all(np.isfinite(m))):
        return math.nan
    return float(np.prod(1 + r) - np.prod(1 + m))


def trade_car(e: EventAR, horizon: int) -> float:
    """Excess return from the entry close to ``horizon`` sessions later; NaN if unknown.

    Also NaN when the stock did not trade on the entry session: there was no close to buy at.
    """
    if not 0 <= e.entry <= MAX_ENTRY_LAG or not e.tradable:
        return math.nan
    return _excess(e, e.entry + 1, e.entry + horizon)


def excess_path(e: EventAR) -> FloatArray:
    """Cumulative excess return over the chart window, starting at ``PATH_WINDOW[0]``."""
    n = PATH_WINDOW[1] - PATH_WINDOW[0] + 1
    if e.ret is None or e.mkt is None:
        return np.asarray(np.cumsum(e.ar[:n]), dtype=np.float64)
    return np.asarray(np.cumprod(1 + e.ret[:n]) - np.cumprod(1 + e.mkt[:n]), dtype=np.float64)


# --------------------------------------------------------------------------- statistics


def _seed(*parts: object) -> int:
    h = hashlib.sha256("|".join(map(str, (SEED, *parts))).encode()).digest()
    return int.from_bytes(h[:8], "big")


def _codes(groups: list[str]) -> tuple[npt.NDArray[np.int64], int]:
    """Integer code of each observation's cluster, and the number of clusters."""
    pos = {n: i for i, n in enumerate(sorted(set(groups)))}
    return np.array([pos[g] for g in groups], dtype=np.int64), len(pos)


def _boot_weights(
    rng: np.random.Generator, groups: list[str], groups2: list[str] | None, draws: int
) -> FloatArray:
    """Bootstrap weights (draws x observations) for resampling whole clusters.

    One way: each draw resamples the clusters with replacement, and an observation's weight
    is how often its cluster was drawn. Two ways (pigeonhole bootstrap): both sets of clusters
    are resampled independently and the weights multiply.
    """
    idx, g = _codes(groups)
    w = rng.multinomial(g, np.full(g, 1.0 / g), size=draws)[:, idx].astype(np.float64)
    if groups2 is not None:
        idx2, g2 = _codes(groups2)
        w *= rng.multinomial(g2, np.full(g2, 1.0 / g2), size=draws)[:, idx2]
    return w


def _boot_means(
    x: FloatArray, groups: list[str], groups2: list[str] | None, seed: int
) -> FloatArray:
    """Bootstrap distribution of the mean of ``x`` (a vector, or events x days)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(0, BOOTSTRAP_DRAWS, 200):  # in chunks, to bound memory
        w = _boot_weights(rng, groups, groups2, min(200, BOOTSTRAP_DRAWS - i))
        s = w.sum(axis=1)
        ok = s > 0
        out.append((w[ok] @ x) / (s[ok] if x.ndim == 1 else s[ok][:, None]))
    return np.concatenate(out)


def bootstrap_ci_clustered(
    values: FloatArray,
    groups: list[str],
    key: str,
    alpha: float = 0.05,
    groups2: list[str] | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean, resampling whole clusters (one or two ways)."""
    means = _boot_means(values, groups, groups2, _seed(key))
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def path_band_clustered(
    mat: FloatArray,
    groups: list[str],
    key: str,
    alpha: float = 0.05,
    groups2: list[str] | None = None,
) -> tuple[FloatArray, FloatArray]:
    """Percentile bootstrap band of the mean path (events x days), resampling clusters."""
    boot = _boot_means(mat, groups, groups2, _seed(key, "path"))
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2], axis=0)
    return lo, hi


def _cluster_var(dev: FloatArray, groups: list[str]) -> tuple[float, int]:
    """CR1 variance of a sum of deviations, clustered by ``groups``; and the cluster count."""
    idx, g = _codes(groups)
    if g < 2:
        return math.nan, g
    score = np.bincount(idx, weights=dev, minlength=g)
    return g / (g - 1) * float((score**2).sum()), g


def cluster_t(
    values: FloatArray, groups: list[str], groups2: list[str] | None = None
) -> tuple[float, float, int]:
    """t-statistic of the mean with cluster-robust (CR1) standard error; (t, p, clusters).

    One way: ``se² = G/(G−1) · Σ_g (Σ_{i∈g} (x_i − x̄))² / n²`` with ``G − 1`` degrees of
    freedom; with one observation per cluster this is the usual standard error of the mean.
    Two ways (Cameron, Gelbach and Miller): the variances by each grouping add and the one by
    their intersection is subtracted; degrees of freedom come from the smaller cluster count.
    """
    n = len(values)
    dev = values - values.mean()
    var, g = _cluster_var(dev, groups)
    if g < 2:
        return math.nan, math.nan, g
    if groups2 is not None:
        var2, g2 = _cluster_var(dev, groups2)
        if g2 >= 2:
            both = [f"{a}|{b}" for a, b in zip(groups, groups2, strict=True)]
            two_way = var + var2 - _cluster_var(dev, both)[0]
            var = two_way if two_way > 0 else max(var, var2)
            g = min(g, g2)
    se = math.sqrt(var) / n
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


@dataclass(frozen=True)
class Trades:
    """Gross excess returns of a set of trades, with what is needed to cost and cluster them."""

    ret: FloatArray
    stocks: list[str]
    months: list[str]
    cost: FloatArray


def _trade_returns(evs: list[EventAR], horizon: int, known_before: int | None = None) -> Trades:
    """Excess return from the entry close to ``horizon`` sessions later, per event.

    With ``known_before``, only trades whose exit session lies before that calendar
    position count, so a rule is never chosen with returns that were still in the future.
    """
    vals: list[float] = []
    stocks: list[str] = []
    months: list[str] = []
    costs: list[float] = []
    for e in evs:
        if known_before is not None and not 0 <= exit_index(e, horizon) < known_before:
            continue
        v = trade_car(e, horizon)
        if math.isfinite(v):
            vals.append(v)
            stocks.append(e.isin)
            months.append(e.month)
            costs.append(e.cost)
    return Trades(
        np.array(vals, dtype=np.float64), stocks, months, np.array(costs, dtype=np.float64)
    )


def choose_rule(train: list[EventAR], known_before: int | None = None) -> Rule | None:
    """Pick the (direction, horizon) with the best t-statistic of net returns on ``train``.

    Ranking by raw mean would favour the noisiest (longest) horizon by chance, so we rank by
    ``mean_net / standard_error``. If no candidate has a positive past net mean, return
    ``None`` (don't trade that year).
    """
    best: tuple[float, Rule] | None = None
    for h in HORIZONS:
        tr = _trade_returns(train, h, known_before)
        r, cost = tr.ret, tr.cost
        if len(r) < MIN_TRAIN_EVENTS:
            continue
        se = float(np.std(r, ddof=1)) / math.sqrt(len(r))
        for d in (1, -1):
            net = float(np.mean(d * r - cost))
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
    oos, _, _, rules = walk_forward_trades(evs, calendar)
    return oos, rules


def walk_forward_trades(
    evs: list[EventAR], calendar: list[date] | None = None
) -> tuple[FloatArray, list[str], list[str], list[Rule]]:
    """:func:`walk_forward` plus the stock and month of every trade (for clustering)."""
    years = sorted({e.event_date.year for e in evs})
    oos: list[float] = []
    stocks: list[str] = []
    months: list[str] = []
    rules: list[Rule] = []
    for y in years:
        train = [e for e in evs if e.event_date.year < y]
        test = [e for e in evs if e.event_date.year == y]
        cutoff = bisect.bisect_left(calendar, date(y, 1, 1)) if calendar is not None else None
        rule = choose_rule(train, cutoff)
        if rule is None:
            continue
        tr = _trade_returns(test, rule.horizon)
        oos.extend((rule.direction * tr.ret - tr.cost).tolist())
        stocks.extend(tr.stocks)
        months.extend(tr.months)
        rules.append(rule)
    return np.array(oos, dtype=np.float64), stocks, months, rules


def oos_p_value(oos: FloatArray, stocks: list[str], months: list[str] | None = None) -> float:
    """One-sided p-value that the mean out-of-sample net return is above zero.

    Cluster-robust like the in-sample test (by stock, and by month when given). NaN with
    fewer than two stocks.
    """
    if len(oos) < 2:
        return math.nan
    t, _, g = cluster_t(oos, stocks, months)
    if not math.isfinite(t):
        return math.nan
    return float(sps.t.sf(t, df=g - 1))


def _sharpe(r: FloatArray) -> float | None:
    """Per-trade Sharpe ratio, or None when it is undefined."""
    if len(r) < 3 or np.std(r) == 0:
        return None
    return float(np.mean(r) / np.std(r, ddof=1))


def trial_sharpes(by_key: dict[str, list[EventAR]]) -> list[float]:
    """Per-trade Sharpe ratio of every (signal, horizon) the walk-forward could have picked.

    Measured on the long side. The short side of the same pair is its mirror image, not an
    independent strategy: it counts as a trial (see ``compute_signal_stats``) but would only
    double the spread, so it is left out of the variance.
    """
    out: list[float] = []
    for key in sorted(by_key):
        for h in HORIZONS:
            sr = _sharpe(_trade_returns(by_key[key], h).ret)
            if sr is not None:
                out.append(sr)
    return out


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
    "oos_p_value": pl.Float64,
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
    wf: dict[str, tuple[FloatArray, list[str], list[str], list[Rule]]] = {}
    for key in sorted(by_key):
        evs = sorted(by_key[key], key=lambda e: (e.event_date, e.report_id))
        wf[key] = walk_forward_trades(evs, calendar)
        for h in HORIZONS:
            tr = _trade_returns(evs, h)
            cars, groups, months = tr.ret, tr.stocks, tr.months
            n = len(cars)
            row: dict[str, Any] = {"as_of": inputs.as_of, "signal_key": key, "horizon": h, "n": n}
            if n >= MIN_EVENTS_FOR_STATS and len(set(groups)) >= MIN_CLUSTERS:
                t, p_val, _ = cluster_t(cars, groups, months)
                lo, hi = bootstrap_ci_clustered(cars, groups, f"{key}|{h}", groups2=months)
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
        # average [-5, +20] excess-return path with a bootstrap band (clusters resampled)
        lo_d, hi_d = PATH_WINDOW
        mat = np.array([excess_path(e) for e in evs])
        full = np.all(np.isfinite(mat), axis=1) if len(mat) else np.zeros(0, dtype=bool)
        mat = mat[full] if len(mat) else mat
        kept = [e for e, ok in zip(evs, full, strict=True) if ok]
        path_stocks = [e.isin for e in kept]
        if len(mat) >= MIN_EVENTS_FOR_STATS and len(set(path_stocks)) >= MIN_CLUSTERS:
            band_lo, band_hi = path_band_clustered(
                mat, path_stocks, key, groups2=[e.month for e in kept]
            )
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

    # deflated Sharpe: one trial per (key, horizon, direction) the walk-forward could pick.
    # Without at least two trials there is nothing to deflate by, so no ratio is reported.
    srs = trial_sharpes(by_key)
    sr_var = float(np.var(srs, ddof=1)) if len(srs) > 1 else math.nan
    n_trials = 2 * len(srs)  # each pair can be traded long or short

    extra = []
    for key in sorted(by_key):
        oos, stocks, oos_months, rules = wf[key]
        last = rules[-1] if rules else None
        dsr = deflated_sharpe(oos, n_trials, sr_var) if math.isfinite(sr_var) else math.nan
        oos_p = oos_p_value(oos, stocks, oos_months)
        extra.append(
            {
                "signal_key": key,
                "oos_n": len(oos),
                "oos_mean_net": float(np.mean(oos)) if len(oos) else None,
                "oos_p_value": None if math.isnan(oos_p) else oos_p,
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
            "oos_p_value": pl.Float64,
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
    positive = pl.col("oos_mean_net") > 0
    confirmed = pl.col("oos_p_value").fill_null(1.0) < SIGNIFICANCE
    df = (
        df.join(best, on="signal_key", how="left")
        .with_columns(
            pl.when(~pl.col("has_stats"))
            .then(pl.lit("too_few"))
            .when(significant & ~tested)
            .then(pl.lit("untested"))
            .when(significant & positive & confirmed)
            .then(pl.lit("holds_up"))
            .when(significant & positive)
            .then(pl.lit("unconfirmed"))
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
                "path": [float(x) for x in excess_path(e)],
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
