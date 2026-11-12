"""The decision layer (L4 in DECISIONS, "One source of truth"): a weekly basket.

It reads only what the forecast (``pick.py``) produced for each week: every eligible stock's
expected excess over WIG, its round-trip cost and its forecast σ. Nothing here forecasts.

* **Which names.** Up to ``K`` stocks with a positive expected excess *after* their own
  round-trip cost, best first. A stock already held stays while it is still in the best
  ``BUFFER × K`` and still positive after cost, so a small change in rank does not cost a
  trade.
* **How much of each.** Hierarchical risk parity (López de Prado 2016) on the covariance of
  the names' residual returns over the last ``COV_WINDOW`` sessions (the market state's
  Kalman residuals; returns against WIG without it), shrunk halfway to its diagonal. HRP
  needs no matrix inversion, so ten names with a few months of data give stable weights.
  No name gets more than ``MAX_WEIGHT``. With fewer than ``K`` names the basket invests
  ``names / K`` and leaves the rest in WIG, which earns nothing against WIG and costs nothing.
* **Costs.** Every increase of a weight pays that stock's round-trip cost on the increase,
  the same rule as the single pick (a purchase pays its way in and out). Between weekly
  rebalances the weights are taken as constant.

Everything is a function of its inputs; the walk-forward and the nightly run use the same
``choose`` and ``weights``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from undercurrent.pick_features import Panel, hold_excess

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

K = 10
BUFFER = 2
MAX_WEIGHT = 0.25
COV_WINDOW = 120
MIN_COV_DAYS = 60
SHRINK = 0.5


@dataclass(frozen=True)
class Forecast:
    """One week of the forecast layer's output, as the basket sees it."""

    t: int
    day: date
    nxt: int  # the next rebalance session (where this week's holding ends)
    stocks: IntArray  # panel rows
    expected: FloatArray
    cost: FloatArray
    scale: FloatArray  # forecast 20-session σ


def choose(
    held: dict[int, float], stocks: IntArray, expected: FloatArray, cost: FloatArray
) -> list[int]:
    """Panel rows to hold this week (see the module docstring)."""
    net = expected - cost
    order = [int(i) for i in np.argsort(-net, kind="stable") if net[i] > 0]
    buffer = set(order[: BUFFER * K])
    keep = [int(stocks[i]) for i in order if int(stocks[i]) in held and i in buffer]
    out = keep[:K]
    for i in order:
        if len(out) >= K:
            break
        if int(stocks[i]) not in out:
            out.append(int(stocks[i]))
    return out


def daily_residuals(panel: Panel) -> FloatArray:
    """Stocks × sessions residual returns: the market state's, else the return minus WIG's."""
    n_known = np.diff(panel.n_resid, axis=1)
    resid = np.where(n_known > 0, np.diff(panel.resid, axis=1), np.nan)
    if np.isfinite(resid).any():
        return np.asarray(resid, dtype=np.float64)
    return np.asarray(panel.ret - panel.mkt[None, :], dtype=np.float64)


def covariance(resid: FloatArray, rows: list[int], t: int, seg: IntArray) -> FloatArray | None:
    """Shrunk covariance of ``rows``' residual returns over the window ending at ``t``."""
    lo = max(0, t - COV_WINDOW + 1)
    while lo < t and seg[lo] != seg[t]:
        lo += 1
    x = resid[rows, lo : t + 1]
    known = np.isfinite(x)
    if known.sum(axis=1).min(initial=0) < MIN_COV_DAYS:
        return None
    x0 = np.where(known, x, 0.0)
    both = known.astype(float) @ known.T.astype(float)
    s = (x0 @ x0.T) / np.maximum(both, 1.0)
    d = np.diag(np.diag(s))
    return np.asarray(SHRINK * s + (1 - SHRINK) * d, dtype=np.float64)


def _cluster_var(cov: FloatArray, idx: list[int]) -> float:
    sub = cov[np.ix_(idx, idx)]
    ivp = 1 / np.diag(sub)
    ivp /= ivp.sum()
    return float(ivp @ sub @ ivp)


def hrp(cov: FloatArray) -> FloatArray:
    """Hierarchical risk parity weights (sum 1) for a covariance matrix."""
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    sd = np.sqrt(np.diag(cov))
    corr = np.clip(cov / np.outer(sd, sd), -1.0, 1.0)
    dist = np.sqrt(np.maximum((1 - corr) / 2, 0.0))
    np.fill_diagonal(dist, 0.0)
    order = [int(i) for i in leaves_list(linkage(squareform(dist, checks=False), "single"))]
    w = np.ones(n)
    clusters = [order]
    while clusters:
        nxt = []
        for c in clusters:
            if len(c) < 2:
                continue
            a, b = c[: len(c) // 2], c[len(c) // 2 :]
            va, vb = _cluster_var(cov, a), _cluster_var(cov, b)
            share = 1 - va / (va + vb)
            w[a] *= share
            w[b] *= 1 - share
            nxt += [a, b]
        clusters = nxt
    return np.asarray(w / w.sum(), dtype=np.float64)


def _cap(w: FloatArray, cap: float) -> FloatArray:
    """Weights summing to ``w.sum()`` with none above ``cap`` (excess spread pro rata)."""
    total = float(w.sum())
    w = w.copy()
    for _ in range(len(w)):
        over = w > cap + 1e-12
        if not over.any():
            break
        spare = float((w[over] - cap).sum())
        w[over] = cap
        free = ~over & (w < cap)
        if not free.any():
            break
        w[free] += spare * w[free] / w[free].sum()
    return np.asarray(w * (total / w.sum()) if w.sum() > 0 else w, dtype=np.float64)


def weights(
    panel: Panel, resid: FloatArray, t: int, names: list[int], sigma: dict[int, float]
) -> dict[int, float]:
    """Weight of each name: HRP on the residual covariance, inverse variance without one."""
    if not names:
        return {}
    cov = covariance(resid, names, t, panel.seg)
    if cov is None or not np.all(np.diag(cov) > 0):
        var = np.array([max(sigma.get(i, 0.0), 1e-4) ** 2 for i in names])
        w = (1 / var) / (1 / var).sum()
    else:
        w = hrp(cov)
    invested = min(1.0, len(names) / K)
    w = _cap(w * invested, MAX_WEIGHT)
    return {i: float(x) for i, x in zip(names, w, strict=True)}


def trade_cost(old: dict[int, float], new: dict[int, float], cost: dict[int, float]) -> float:
    """Round-trip cost of every increase of a weight."""
    return float(sum(max(w - old.get(i, 0.0), 0.0) * cost.get(i, 0.0) for i, w in new.items()))


def step(
    panel: Panel, resid: FloatArray, f: Forecast, held: dict[int, float]
) -> tuple[dict[int, float], float]:
    """The week's weights, and the cost of getting there from ``held``."""
    names = choose(held, f.stocks, f.expected, f.cost)
    pos = {int(s): k for k, s in enumerate(f.stocks)}
    sigma = {i: float(f.scale[pos[i]]) for i in names}
    new = weights(panel, resid, f.t, names, sigma)
    costs = {int(s): float(c) for s, c in zip(f.stocks, f.cost, strict=True)}
    return new, trade_cost(held, new, costs)


def walk(
    panel: Panel, forecasts: list[Forecast]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Weekly basket results (gross, cost, net against WIG) and the weights behind them."""
    resid = daily_residuals(panel)
    held: dict[int, float] = {}
    rows: list[dict[str, object]] = []
    wrows: list[dict[str, object]] = []
    for f in forecasts:
        new, cost = step(panel, resid, f, held)
        g = [(w, hold_excess(panel, i, f.t, f.nxt)) for i, w in new.items()]
        known = [(w, x) for w, x in g if math.isfinite(x)]
        gross = float(sum(w * x for w, x in known)) if known or not g else math.nan
        turnover = float(sum(abs(new.get(i, 0.0) - held.get(i, 0.0)) for i in set(new) | set(held)))
        rows.append(
            {
                "day": f.day,
                "names": len(new),
                "invested": float(sum(new.values())),
                "turnover": turnover,
                "cost": cost,
                "gross": gross,
                "net": gross - cost,
            }
        )
        wrows += [
            {"day": f.day, "isin": panel.isins[i], "ticker": panel.tickers[i], "weight": w}
            for i, w in sorted(new.items(), key=lambda kv: -kv[1])
        ]
        held = new
    return rows, wrows
