"""A stock's risk on its page: value at risk, expected shortfall, drawdown, how calm it is.

Everything starts from the market state's one-session σ forecast (``vol.py``: GARCH, the day's
range), known at the previous close, so these figures are tomorrow's, not yesterday's.

* **Fat tails.** Daily returns divided by that σ (``z``) are closer to a Student-t than to a
  normal. ``fit_nu`` picks the degrees of freedom ν from ``NU_GRID`` by the likelihood of a
  *standardised* t (unit variance) on the stock's last ``FIT_SESSIONS`` values of z. A grid
  cannot fail to converge and gives the same ν on every machine.
* **Unit variance.** σ is the standard deviation of the return, so the t quantile is scaled
  by √((ν − 2) / ν): a plain t with ν = 5 has a standard deviation of 1.29, and using its
  quantile as if it had 1 overstates the VaR by 29 %.
* **VaR and ES.** One session, as a loss share of the price: VaR at 95 % and 99 %, and the
  expected shortfall at 97.5 % (the mean loss beyond that quantile, the Basel measure).
* **Backtest.** For the last ``BACKTEST`` sessions, each session's VaR from the σ known the
  evening before and a ν fitted on the sessions *before* the backtest year, so the year is
  out of sample. Exceedances against the expected 5 % and 1 %, with Kupiec's
  proportion-of-failures test.
* **Drawdown.** The adjusted close against its highest since the last hole in the lake, and
  the deepest fall from a high within the last ``BACKTEST`` sessions.
* **Calm or not.** Today's σ as a percentile of the stock's own σ over the last year.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import stats as sps

FloatArray = npt.NDArray[np.float64]

NU_GRID = (3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 30.0)
FIT_SESSIONS = 500
MIN_FIT = 150
BACKTEST = 250
LEVELS = (0.95, 0.99)
ES_LEVEL = 0.975


def _scale(nu: float) -> float:
    return math.sqrt((nu - 2.0) / nu)


def fit_nu(z: FloatArray) -> float | None:
    """Degrees of freedom of a unit-variance Student-t, by likelihood on a grid."""
    z = z[np.isfinite(z)]
    if len(z) < MIN_FIT:
        return None
    best, best_ll = None, -math.inf
    for nu in NU_GRID:
        s = _scale(nu)
        ll = float(np.sum(sps.t.logpdf(z / s, nu)) - len(z) * math.log(s))
        if ll > best_ll:
            best, best_ll = nu, ll
    return best


def quantile(p: float, nu: float | None) -> float:
    """The ``p`` quantile of a unit-variance t (normal without ν); negative below 0.5."""
    if nu is None:
        return float(sps.norm.ppf(p))
    return float(sps.t.ppf(p, nu)) * _scale(nu)


def shortfall(level: float, nu: float | None) -> float:
    """Expected loss beyond the ``level`` VaR of a unit-variance t (normal without ν), > 0."""
    a = 1.0 - level
    if nu is None:
        return float(sps.norm.pdf(sps.norm.ppf(a)) / a)
    q = float(sps.t.ppf(a, nu))
    return float(sps.t.pdf(q, nu) / a * (nu + q * q) / (nu - 1.0)) * _scale(nu)


def kupiec(n: int, x: int, p: float) -> float | None:
    """p-value of Kupiec's test that ``x`` exceedances in ``n`` sessions have rate ``p``."""
    if n <= 0:
        return None
    phat = x / n

    def ll(q: float) -> float:
        out = 0.0
        if x:
            out += x * math.log(q) if q > 0 else -math.inf
        if n - x:
            out += (n - x) * math.log(1 - q) if q < 1 else -math.inf
        return out

    lr = -2.0 * (ll(p) - ll(phat))
    return float(sps.chi2.sf(max(lr, 0.0), 1))


def standardised(ret: FloatArray, sigma: FloatArray) -> FloatArray:
    """Each session's return over the σ forecast the evening before (NaN where unknown)."""
    z = np.full(len(ret), np.nan)
    if len(ret) > 1:
        prev = sigma[:-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            z[1:] = np.where(prev > 0, ret[1:] / prev, np.nan)
    return z


def profile(
    ret: FloatArray, sigma: FloatArray, adj_close: FloatArray, seg: npt.NDArray[np.int64]
) -> dict[str, Any] | None:
    """The risk figures for the last session of one stock (None without a σ forecast).

    ``ret``: daily returns (NaN where none), ``sigma``: the market state's one-session σ
    forecast known at each session's close, ``adj_close``, ``seg``: calendar segments.
    """
    if not len(sigma) or not math.isfinite(sigma[-1]) or sigma[-1] <= 0:
        return None
    z = standardised(ret, sigma)
    nu = fit_nu(z[-FIT_SESSIONS:])
    s = float(sigma[-1])
    out: dict[str, Any] = {
        "nu": nu,
        "sigma": s,
        "var95": -s * quantile(1 - 0.95, nu),
        "var99": -s * quantile(1 - 0.99, nu),
        "es975": s * shortfall(ES_LEVEL, nu),
    }

    # the backtest year, with ν from the sessions before it
    bt_nu = fit_nu(z[-BACKTEST - FIT_SESSIONS : -BACKTEST]) if len(z) > BACKTEST else None
    prev_sigma = np.concatenate([[np.nan], sigma[:-1]])
    r, ps = ret[-BACKTEST:], prev_sigma[-BACKTEST:]
    ok = np.isfinite(r) & np.isfinite(ps) & (ps > 0)
    n = int(ok.sum())
    bt: dict[str, Any] = {"n": n, "nu": bt_nu}
    for level in LEVELS:
        x = int(np.sum(r[ok] < ps[ok] * quantile(1 - level, bt_nu)))
        key = f"{round(level * 100)}"
        bt[f"x{key}"] = x
        bt[f"p{key}"] = kupiec(n, x, 1 - level) if n >= MIN_FIT else None
    out["backtest"] = bt

    in_seg = seg == seg[-1]
    c = adj_close[in_seg]
    out["drawdown"] = float(c[-1] / np.max(c) - 1.0) if len(c) else None
    year = c[-BACKTEST:]
    out["max_drawdown_year"] = (
        float(np.min(year / np.maximum.accumulate(year) - 1.0)) if len(year) else None
    )
    sig_year = sigma[-BACKTEST:]
    sig_year = sig_year[np.isfinite(sig_year)]
    out["vol_percentile"] = float(np.mean(sig_year <= s)) if len(sig_year) >= MIN_FIT else None
    return out
