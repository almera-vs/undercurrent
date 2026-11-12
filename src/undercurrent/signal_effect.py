"""What each kind of report is expected to do next, shrunk by its evidence (L2 in DECISIONS).

The event study (``study.py``) measures how the stock did against WIG over the 20 sessions
after the entry close, for every kind of report. Here that becomes an input of the forecast:

* **Point in time.** The estimate used during calendar year Y comes only from events whose
  20-session trade had closed before Y began, the same purge as the walk-forward.
* **Standard errors** clustered by stock and by calendar month, as in the study.
* **Shrunk by its evidence, in two steps** (empirical Bayes, partial pooling):

  1. What all reports have in common. Every event of every kind, pooled, gives a mean μ
     with a two-way clustered t-statistic. On 2015–2018 all kinds drift the same way against
     WIG (small caps with news against a large-cap index), so most of what a single kind
     shows is this common part. μ is kept at the share ``max(0, 1 − 1/t²)``: about nothing
     when it is within noise, most of it when it is many standard errors from 0.
  2. What a kind adds. The kinds' true deviations from μ are taken as drawn from N(0, τ²),
     with τ² the spread of the kinds' means beyond what their standard errors explain
     (never below 0). A kind keeps the share τ² / (τ² + se²) of its own deviation.

  A kind's effect is the kept μ plus its kept deviation. Kinds with fewer than
  ``MIN_EVENTS`` events or ``MIN_STOCKS`` stocks, or with fewer than ``MIN_KINDS`` kinds to
  compare, count as the kept μ alone.
* **Per stock and session.** ``signal_effect`` sums, over the kinds of report whose
  20-session window is still running for the stock, the kind's effect times the share of the
  window still ahead, counting only the latest report of each kind: a stock that files an
  insider notice every day for a week has one insider effect, not five. An event counts from
  its entry session, when a reader of the digest could first act on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl

from undercurrent import study
from undercurrent.vol import year_starts

FloatArray = npt.NDArray[np.float64]

HORIZON = 20
MIN_EVENTS = study.MIN_EVENTS_FOR_STATS
MIN_STOCKS = study.MIN_CLUSTERS
MIN_KINDS = 3  # kinds with a standard error needed to estimate τ²
COMMON = "*"  # the key of the pooled, common part


@dataclass(frozen=True)
class Effect:
    """One kind of report's estimate for one calendar year."""

    key: str
    year: int
    n: int
    stocks: int
    mean: float
    se: float
    common: float  # the kept common part μ*
    pooled: float  # μ, the deviation's anchor
    shrink: float  # share of the deviation from μ kept, τ² / (τ² + se²)

    @property
    def shrunk(self) -> float:
        """The effect the forecast uses."""
        return self.common + self.shrink * (self.mean - self.pooled)


@dataclass(frozen=True)
class Event:
    """What the effect needs to know about one event."""

    key: str
    isin: str
    entry_t: int  # calendar position of the entry session
    exit_t: int  # calendar position of the exit session of a 20-session trade
    car: float  # excess return over (entry, entry + 20]; NaN if not known


def events_from(
    ars: list[study.EventAR], study_cal: list[date], cal: list[date] | None = None
) -> list[Event]:
    """The deduplicated events with a usable entry, positioned on ``cal``.

    ``study_cal`` is the calendar the event study indexed (sessions with a WIG return); a
    target calendar ``cal`` may also hold the first session after each hole, so positions are
    carried over by date. An exit beyond the study's calendar is not known yet.
    """
    target = {d: i for i, d in enumerate(cal if cal is not None else study_cal)}
    far = len(target) + HORIZON
    out = []
    for e in study.dedupe_events(ars):
        exit_s = study.exit_index(e, HORIZON)
        entry_s = e.day0_idx + e.entry
        if exit_s < 0 or not 0 <= entry_s < len(study_cal):
            continue
        entry_t = target.get(study_cal[entry_s])
        if entry_t is None:
            continue
        exit_t = target.get(study_cal[exit_s], far) if exit_s < len(study_cal) else far
        out.append(Event(e.signal_key, e.isin, entry_t, exit_t, study.trade_car(e, HORIZON)))
    return out


def _mean_se(evs: list[Event], cal: list[date]) -> tuple[float, float] | None:
    x = np.array([e.car for e in evs], dtype=np.float64)
    months = [f"{cal[min(e.entry_t, len(cal) - 1)]:%Y-%m}" for e in evs]
    t, _, _ = study.cluster_t(x, [e.isin for e in evs], months)
    mean = float(x.mean())
    if not math.isfinite(t) or t == 0:
        return None
    return mean, abs(mean / t)


def estimate(events: list[Event], cut_t: int, year: int, cal: list[date]) -> dict[str, Effect]:
    """Effects from the events whose trade closed before calendar position ``cut_t``.

    The result also holds the common part alone under ``COMMON``.
    """
    known = [e for e in events if e.exit_t < cut_t and math.isfinite(e.car)]
    if len(known) < MIN_EVENTS or len({e.isin for e in known}) < MIN_STOCKS:
        return {}
    pooled = _mean_se(known, cal)
    if pooled is None:
        return {}
    mu, mu_se = pooled
    keep = max(0.0, 1 - (mu_se / mu) ** 2) if mu != 0 else 0.0
    common = keep * mu
    by_key: dict[str, list[Event]] = {}
    for e in known:
        by_key.setdefault(e.key, []).append(e)
    raw: dict[str, tuple[int, int, float, float]] = {}
    for key, evs in sorted(by_key.items()):
        stocks = len({e.isin for e in evs})
        if len(evs) < MIN_EVENTS or stocks < MIN_STOCKS:
            continue
        ms = _mean_se(evs, cal)
        if ms is not None:
            raw[key] = (len(evs), stocks, ms[0], ms[1])
    tau2 = 0.0
    if len(raw) >= MIN_KINDS:
        dev = np.array([v[2] - mu for v in raw.values()])
        ses = np.array([v[3] for v in raw.values()])
        tau2 = max(0.0, float(np.mean(dev**2) - np.mean(ses**2)))
    out = {
        k: Effect(k, year, n, s, m, se, common, mu, tau2 / (tau2 + se**2) if tau2 > 0 else 0.0)
        for k, (n, s, m, se) in raw.items()
    }
    out[COMMON] = Effect(
        COMMON, year, len(known), len({e.isin for e in known}), mu, mu_se, common, mu, 0.0
    )
    return out


def effects_by_year(events: list[Event], cal: list[date]) -> dict[int, dict[str, Effect]]:
    """The effects in use during each calendar year of ``cal``."""
    return {cal[s].year: estimate(events, s, cal[s].year, cal) for s, _ in year_starts(cal)}


def effect_of(effs: dict[str, Effect], key: str) -> float:
    """A kind's effect in one year: its own estimate, else the kept common part."""
    if key in effs:
        return effs[key].shrunk
    return effs[COMMON].common if COMMON in effs else 0.0


def daily_effect(
    events: list[Event], cal: list[date], isins: list[str]
) -> tuple[FloatArray, dict[int, dict[str, Effect]]]:
    """Stocks × sessions ``signal_effect`` (0 where no event is running) and the estimates."""
    pos = {s: i for i, s in enumerate(isins)}
    out = np.zeros((len(isins), len(cal)))
    by_year = effects_by_year(events, cal)
    year_of = [d.year for d in cal]
    latest: dict[tuple[int, str, int], int] = {}  # (stock, kind, session) → latest entry
    for e in events:
        i = pos.get(e.isin)
        if i is None:
            continue
        for t in range(e.entry_t, min(e.entry_t + HORIZON, len(cal))):
            k = (i, e.key, t)
            latest[k] = max(latest.get(k, -1), e.entry_t)
    for (i, key, t), entry in latest.items():
        out[i, t] += effect_of(by_year[year_of[t]], key) * (entry + HORIZON - t) / HORIZON
    return out, by_year


def effects_frame(by_year: dict[int, dict[str, Effect]]) -> pl.DataFrame:
    """Every year's estimates, for the record and the event pages."""
    rows = [
        {
            "year": eff.year,
            "signal_key": eff.key,
            "n": eff.n,
            "stocks": eff.stocks,
            "mean": eff.mean,
            "se": eff.se,
            "common": eff.common,
            "shrink": eff.shrink,
            "shrunk": eff.shrunk if eff.key != COMMON else eff.common,
        }
        for effs in by_year.values()
        for eff in effs.values()
    ]
    schema = {
        "year": pl.Int64,
        "signal_key": pl.String,
        "n": pl.Int64,
        "stocks": pl.Int64,
        "mean": pl.Float64,
        "se": pl.Float64,
        "common": pl.Float64,
        "shrink": pl.Float64,
        "shrunk": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema).sort(["year", "signal_key"])
