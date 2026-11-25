"""The decision layer: a weekly basket.

It reads only what the forecast (``pick.py``) produced for each week: every eligible stock's
expected excess over WIG and its round-trip cost. Nothing here forecasts.

A rank band:

* **Which names.** The ``N`` stocks with the highest forecast are bought. A stock already
  held stays while it is still among the best ``KEEP``; only then is it sold, and the best
  stock not yet held takes its place. The ranking is all the rule reads: the forecast's
  level (its intercept, the universe's past drift against WIG) never decides whether to
  invest, because the model has no skill in it.
* **Why a band.** The forecast's edge fades slowly (out of sample, the best ten's excess over
  the universe is about 1 % in the first 4 weeks and 2.8 % in 20), while a round trip costs
  1.2–2 %. Buying a stock pays only if it is held for months, so a stock is kept while it is
  still clearly above average, not sold when it slips out of the top ``N``. A held stock's
  cost was paid when it was bought; it never counts against keeping it.
* **How much of each.** ``1 / N`` each, always fully invested. Weighting by risk gave the
  steadier stocks more and the forecast's edge is not in them; with 15 names and noisy
  forecasts equal weights are the robust choice.
* **Costs.** Every increase of a weight pays that stock's round-trip cost on the increase (a
  purchase pays its way in and out). Between weekly rebalances the weights are taken as
  constant.
* **Against what.** The result is measured against WIG (what an investor could hold
  instead) and against the equal-weighted liquid universe it is chosen from (what the
  ranking adds). The two differ by the universe's own drift against WIG, a size effect the
  ranking does not forecast.

Everything is a function of its inputs; the walk-forward and the nightly run use the same
``choose`` and ``step``.
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

N = 15  # names held
KEEP = 45  # a held name stays while it ranks inside this many


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


def choose(held: dict[int, float], stocks: IntArray, expected: FloatArray) -> list[int]:
    """Panel rows to hold this week, best forecast first (see the module docstring)."""
    order = [int(stocks[i]) for i in np.argsort(-expected, kind="stable")]
    rank = {s: r for r, s in enumerate(order)}
    keep = sorted((s for s in held if rank.get(s, KEEP) < KEEP), key=rank.__getitem__)[:N]
    out = list(keep)
    for s in order:
        if len(out) >= N:
            break
        if s not in keep:
            out.append(s)
    return sorted(out, key=rank.__getitem__)


def weights(names: list[int]) -> dict[int, float]:
    """Equal weights, fully invested."""
    return {i: 1 / len(names) for i in names} if names else {}


def trade_cost(old: dict[int, float], new: dict[int, float], cost: dict[int, float]) -> float:
    """Round-trip cost of every increase of a weight."""
    return float(sum(max(w - old.get(i, 0.0), 0.0) * cost.get(i, 0.0) for i, w in new.items()))


def step(f: Forecast, held: dict[int, float]) -> tuple[dict[int, float], float]:
    """The week's weights, and the cost of getting there from ``held``."""
    new = weights(choose(held, f.stocks, f.expected))
    costs = {int(s): float(c) for s, c in zip(f.stocks, f.cost, strict=True)}
    return new, trade_cost(held, new, costs)


def walk(
    panel: Panel, forecasts: list[Forecast]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Weekly basket results (gross, cost, net against WIG and the universe) and weights."""
    held: dict[int, float] = {}
    rows: list[dict[str, object]] = []
    wrows: list[dict[str, object]] = []
    for f in forecasts:
        new, cost = step(f, held)
        g = [(w, hold_excess(panel, i, f.t, f.nxt)) for i, w in new.items()]
        known = [(w, x) for w, x in g if math.isfinite(x)]
        gross = float(sum(w * x for w, x in known)) if known or not g else math.nan
        univ = [hold_excess(panel, int(i), f.t, f.nxt) for i in f.stocks]
        universe = float(np.nanmean(univ)) if np.isfinite(univ).any() else math.nan
        invested = float(sum(new.values()))
        turnover = float(sum(abs(new.get(i, 0.0) - held.get(i, 0.0)) for i in set(new) | set(held)))
        rows.append(
            {
                "day": f.day,
                "names": len(new),
                "invested": invested,
                "turnover": turnover,
                "cost": cost,
                "gross": gross,
                "net": gross - cost,
                "universe": universe,
                # what the ranking added: net minus the universe's result on the money invested
                "active": gross - cost - invested * universe,
            }
        )
        wrows += [
            {"day": f.day, "isin": panel.isins[i], "ticker": panel.tickers[i], "weight": w}
            for i, w in new.items()
        ]
        held = new
    return rows, wrows
