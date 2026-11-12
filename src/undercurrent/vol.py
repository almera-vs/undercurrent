"""Volatility forecasts for the market state: GARCH(1,1) with an EWMA fallback.

For every stock and session ``t`` the forecast is the variance of the next session's return
given returns up to and including ``t`` (``h[t]`` is known at ``t``'s close).

* **GARCH(1,1) with variance targeting.** ``h[t+1] = ω + α·r[t]² + β·h[t]`` with
  ``ω = v·(1 − α − β)``, where ``v`` is the stock's mean squared return over the training
  window. (α, β) is the point of a fixed grid with the highest Gaussian quasi-likelihood.
  A grid has no optimiser tolerances and cannot fail to converge, so the result is the same
  on every machine; all stocks are fitted at once.
* **Fitted like the pick.** Parameters for calendar year Y come from the ``TRAIN`` sessions
  before Y's first session and are frozen for Y. The filter then runs from the start of that
  window through Y. A forecast at ``t`` uses returns up to ``t`` and parameters from before
  ``t``'s year, so cutting the data at any date leaves every earlier forecast unchanged.
* **Fallback.** A stock with fewer than ``MIN_FIT`` returns in its training window gets the
  RiskMetrics EWMA (λ = 0.94), started from the mean square of its first ``EWMA_WARMUP``
  returns. ``source`` says which estimator produced a value.
* **Missing returns and holes.** A session without a return updates the variance with its
  expectation (``h ← ω + (α + β)·h``; EWMA keeps ``h``). At a hole in the calendar the filter
  restarts: GARCH from ``v``, EWMA from a new warm-up.
* **The day's range** (GARCH-X). A squared daily return is a very noisy measure of that
  day's variance; the open, high and low say much more. With ``rv`` given (``range_variance``:
  the overnight move squared plus the Garman–Klass intraday variance) the recursion is fed
  ``(1 − w)·r² + w·rv`` instead of ``r²``, and ``w`` ∈ ``WEIGHTS`` is a third grid
  dimension, chosen with α and β by the same likelihood of the returns. ``w = 0`` is plain
  GARCH, so the fit can always fall back to it. Sessions without a range use ``r²``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

TRAIN = 750  # sessions of history a yearly fit looks at (about three years)
MIN_FIT = 250  # returns a stock needs in that window for a GARCH fit
EWMA_LAMBDA = 0.94
EWMA_WARMUP = 20
# (0.5 % a day)²: the calmest liquid GPW stocks move about 1 % a day. A stock whose price has
# not changed for weeks would otherwise get a variance near zero, and its next tick would be
# a "surprise" of dozens of standard deviations.
VAR_FLOOR = 0.005**2
INDEX_VAR_FLOOR = 1e-10  # indices (TBSP moves about 0.2 % a day): no practical floor

SOURCE_NONE, SOURCE_GARCH, SOURCE_EWMA = 0, 1, 2
SOURCE_NAMES = {SOURCE_NONE: None, SOURCE_GARCH: "garch", SOURCE_EWMA: "ewma"}

_ALPHAS = np.round(np.arange(0.02, 0.301, 0.02), 4)
_PERSISTENCE = np.array([0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99])


WEIGHTS = (0.0, 0.5, 1.0)  # share of the day's range variance in the innovation
GK_K = 2 * np.log(2) - 1  # Garman–Klass constant


def range_variance(
    log_open: FloatArray,
    log_high: FloatArray,
    log_low: FloatArray,
    log_close: FloatArray,
    ret: FloatArray,
) -> FloatArray:
    """A session's variance from its prices: overnight move² + Garman–Klass intraday variance.

    The overnight move is the session's (adjusted) log return minus its open-to-close move,
    so a corporate action, which the adjusted return already removes, cannot inflate it.
    NaN where any price or the return is missing.
    """
    with np.errstate(invalid="ignore"):
        hl = log_high - log_low
        co = log_close - log_open
        gk = 0.5 * hl**2 - GK_K * co**2
        overnight = np.log1p(ret) - co
        out = overnight**2 + np.maximum(gk, 0.0)
    ok = np.isfinite(out) & np.isfinite(ret) & (hl >= 0)
    return np.asarray(np.where(ok, out, np.nan), dtype=np.float64)


def _grid() -> tuple[FloatArray, FloatArray]:
    a, p = np.meshgrid(_ALPHAS, _PERSISTENCE, indexing="ij")
    a, p = a.ravel(), p.ravel()
    keep = p - a > 0
    return a[keep].astype(np.float64), p[keep].astype(np.float64)


GRID_ALPHA, GRID_PERSISTENCE = _grid()


@dataclass(frozen=True)
class GarchParams:
    """Per-stock GARCH(1,1) parameters; NaN where a stock could not be fitted."""

    alpha: FloatArray
    beta: FloatArray
    target: FloatArray  # unconditional variance v
    weight: FloatArray | None = None  # w, the range's share of the innovation (0 without)

    @property
    def w(self) -> FloatArray:
        """The range weight, 0 where none was fitted."""
        return self.weight if self.weight is not None else np.zeros_like(self.alpha)

    @property
    def fitted(self) -> npt.NDArray[np.bool_]:
        """Stocks with a fit."""
        return np.asarray(np.isfinite(self.alpha), dtype=np.bool_)


@dataclass
class Sigma:
    """Variance forecasts on a stocks × sessions grid."""

    h_next: FloatArray  # variance of the next session's return, known at t's close
    source: npt.NDArray[np.int8]
    persistence: FloatArray  # α + β of the fit behind the value (1 for EWMA)
    target: FloatArray  # v of that fit (the EWMA value itself for EWMA)
    weight: FloatArray | None = None  # the range weight w of the fit behind the value

    def horizon_var(self, horizon: int) -> FloatArray:
        """Variance of the sum of the next ``horizon`` daily returns."""
        p, v, h = self.persistence, self.target, self.h_next
        with np.errstate(invalid="ignore", divide="ignore"):
            decay = np.where(p < 1, (1 - p**horizon) / (1 - p), float(horizon))
        return np.asarray(horizon * v + (h - v) * decay, dtype=np.float64)


def year_starts(cal: list[date]) -> list[tuple[int, int]]:
    """``(start, end)`` calendar positions of each calendar year in ``cal``."""
    starts = [t for t in range(len(cal)) if t == 0 or cal[t].year != cal[t - 1].year]
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(cal)) for k, s in enumerate(starts)]


def _innovation(r2: FloatArray, rv: FloatArray | None, w: FloatArray) -> FloatArray:
    """``(1 − w)·r² + w·rv``, with ``r²`` where the range is missing."""
    if rv is None:
        return r2
    return np.where(np.isfinite(rv), (1 - w) * r2 + w * np.where(np.isfinite(rv), rv, 0.0), r2)


def fit_garch(
    ret: FloatArray,
    seg: IntArray,
    lo: int,
    hi: int,
    floor: float = VAR_FLOOR,
    rv: FloatArray | None = None,
) -> GarchParams:
    """Fit every stock on sessions ``lo .. hi-1`` by grid quasi-likelihood.

    With ``rv`` the range weight ``w`` is fitted too (``WEIGHTS``), else it is 0.
    """
    n = ret.shape[0]
    r = ret[:, lo:hi]
    known = np.isfinite(r)
    cnt = known.sum(axis=1)
    r0 = np.where(known, r, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        target = (r0**2).sum(axis=1) / cnt
    ok = (cnt >= MIN_FIT) & (target > floor)
    if not ok.any() or hi <= lo:
        nan = np.full(n, np.nan)
        return GarchParams(nan, nan.copy(), nan.copy(), nan.copy())
    weights = np.array(WEIGHTS if rv is not None else (0.0,))
    g = len(GRID_ALPHA)
    a = np.tile(GRID_ALPHA, len(weights))[None, :]
    p = np.tile(GRID_PERSISTENCE, len(weights))[None, :]
    w = np.repeat(weights, g)[None, :]
    b = p - a
    tv = np.where(ok, target, 1.0)[:, None]
    omega = tv * (1 - p)
    h = np.repeat(tv, a.shape[1], axis=1)
    ll = np.zeros_like(h)
    s = seg[lo:hi]
    x = rv[:, lo:hi] if rv is not None else None
    for j in range(r.shape[1]):
        if j and s[j] != s[j - 1]:
            h[:] = tv
        r2 = r0[:, j : j + 1] ** 2
        k = known[:, j : j + 1]
        ll -= np.where(k, 0.5 * (np.log(h) + r2 / h), 0.0)
        e2 = _innovation(r2, x[:, j : j + 1] if x is not None else None, w)
        h = omega + np.where(k, a * e2 + b * h, p * h)
    best = np.argmax(ll, axis=1)
    alpha = np.where(ok, a[0, best], np.nan)
    beta = np.where(ok, p[0, best] - a[0, best], np.nan)
    return GarchParams(alpha, beta, np.where(ok, target, np.nan), np.where(ok, w[0, best], np.nan))


def _garch_path(
    ret: FloatArray,
    seg: IntArray,
    prm: GarchParams,
    lo: int,
    hi: int,
    floor: float,
    rv: FloatArray | None = None,
) -> FloatArray:
    """``h_next`` for sessions ``lo .. hi-1`` (NaN for stocks without a fit)."""
    n = ret.shape[0]
    out = np.full((n, hi - lo), np.nan)
    ok = prm.fitted
    if not ok.any():
        return out
    a = np.where(ok, prm.alpha, 0.0)
    b = np.where(ok, prm.beta, 0.0)
    v = np.where(ok, prm.target, 1.0)
    wt = np.where(ok, np.nan_to_num(prm.w), 0.0)
    p = a + b
    omega = v * (1 - p)
    h = v.copy()
    for j, t in enumerate(range(lo, hi)):
        if t > lo and seg[t] != seg[t - 1]:
            h = v.copy()
        r = ret[:, t]
        k = np.isfinite(r)
        e2 = _innovation(np.where(k, r, 0.0) ** 2, rv[:, t] if rv is not None else None, wt)
        h = np.maximum(omega + np.where(k, a * e2 + b * h, p * h), floor)
        out[:, j] = np.where(ok, h, np.nan)
    return out


def ewma_path(ret: FloatArray, seg: IntArray, floor: float = VAR_FLOOR) -> FloatArray:
    """RiskMetrics EWMA ``h_next`` on the whole grid; NaN until the warm-up is complete."""
    n, t_n = ret.shape
    out = np.full((n, t_n), np.nan)
    h = np.full(n, np.nan)
    acc = np.zeros(n)
    cnt = np.zeros(n, dtype=np.int64)
    for t in range(t_n):
        if t and seg[t] != seg[t - 1]:
            h[:] = np.nan
            acc[:] = 0.0
            cnt[:] = 0
        r = ret[:, t]
        k = np.isfinite(r)
        e2 = np.where(k, r, 0.0) ** 2
        warm = ~np.isfinite(h)
        upd = ~warm & k
        h = np.where(upd, EWMA_LAMBDA * h + (1 - EWMA_LAMBDA) * e2, h)
        acc += np.where(warm & k, e2, 0.0)
        cnt += warm & k
        start = warm & (cnt >= EWMA_WARMUP)
        with np.errstate(invalid="ignore", divide="ignore"):
            h = np.where(start, np.maximum(acc / cnt, floor), h)
        h = np.where(upd, np.maximum(h, floor), h)
        out[:, t] = h
    return out


def sigma_forecasts(
    ret: FloatArray,
    seg: IntArray,
    cal: list[date],
    floor: float = VAR_FLOOR,
    rv: FloatArray | None = None,
) -> Sigma:
    """Point-in-time variance forecasts for every stock and session (see module docstring).

    ``floor`` is the smallest variance reported: ``VAR_FLOOR`` for stocks,
    ``INDEX_VAR_FLOOR`` for indices. ``rv`` (``range_variance``) lets the fit use the day's
    range.
    """
    n, t_n = ret.shape
    h_next = np.full((n, t_n), np.nan)
    source = np.zeros((n, t_n), dtype=np.int8)
    persistence = np.full((n, t_n), np.nan)
    target = np.full((n, t_n), np.nan)
    weight = np.full((n, t_n), np.nan)
    ew = ewma_path(ret, seg, floor)
    for start, end in year_starts(cal):
        lo = max(0, start - TRAIN)
        prm = fit_garch(ret, seg, lo, start, floor, rv)
        g = _garch_path(ret, seg, prm, lo, end, floor, rv)[:, start - lo :]
        has_g = np.isfinite(g)
        e = ew[:, start:end]
        has_e = np.isfinite(e)
        h_next[:, start:end] = np.where(has_g, g, e)
        source[:, start:end] = np.where(
            has_g, SOURCE_GARCH, np.where(has_e, SOURCE_EWMA, SOURCE_NONE)
        )
        pers = (prm.alpha + prm.beta)[:, None]
        persistence[:, start:end] = np.where(has_g, pers, np.where(has_e, 1.0, np.nan))
        target[:, start:end] = np.where(has_g, prm.target[:, None], e)
        weight[:, start:end] = np.where(has_g, prm.w[:, None], np.nan)
    return Sigma(h_next, source, persistence, target, weight)
