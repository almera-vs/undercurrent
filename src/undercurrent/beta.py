"""Time-varying factor betas for the market state: a Kalman filter per stock.

Model, for stock ``i`` on session ``t`` with factor returns ``f[t]`` (WIG and TBSP):

    r[t] = f[t] · b[t] + e[t],      e[t] ~ N(0, R[t])
    b[t] = b[t-1] + w[t],            w[t] ~ N(0, Q)

* **No intercept.** An earlier market model with a fitted intercept produced a spurious
  drift (METHODOLOGY § 3); the betas here have none to fit.
* **Noise from the volatility forecasts.** ``R[t]`` is what the stock's GARCH variance
  leaves after the factors: ``h_i − Σ_j b_j² · h_j``, never below ``R_FLOOR · h_i``. All
  variances are the forecasts made at ``t − 1`` (``vol.Sigma.h_next`` shifted by one), so
  the filter at ``t`` uses nothing from ``t`` before it sees ``r[t]``.
* **Drift of the betas.** ``Q`` is diagonal; the WIG beta moves by ``q`` a day (standard
  deviation) and the TBSP beta by ``q`` times the ratio of WIG to TBSP volatility, so both
  move the same amount in units of their factor's risk. ``q`` is chosen per calendar year
  from ``Q_GRID`` by the pooled predictive likelihood of the training window, then frozen,
  as ``vol`` does for GARCH. The first year in the lake uses ``Q_DEFAULT``. The grid stops
  at 0.02 on purpose: on 2015–2018 the likelihood mostly picks that end, because a fast
  beta soaks up fat-tailed noise. A beta that moves more than about ±0.3 in a year (0.02 ×
  √250) is not a beta any more, so the cap is a prior, not a fitted value.
* **Start.** At a stock's first session, and again after every hole, the betas start from
  ``PRIOR`` (1 to WIG, 0 to bonds) with a wide uncertainty and need ``WARMUP`` returns before
  they are reported. A session without a stock return only lets the uncertainty grow.

Outputs per (stock, session): the filtered betas ``b[t|t]``, the innovation
``v[t] = r[t] − f[t]·b[t|t−1]`` (the residual return, known at t's close) and its predictive
variance ``S[t]``, so ``v / √S`` is the session's surprise in standard deviations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from undercurrent.vol import TRAIN, year_starts

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

Q_GRID: tuple[float, ...] = (0.002, 0.005, 0.01, 0.02)
Q_DEFAULT = 0.005
PRIOR = np.array([1.0, 0.0])
PRIOR_SD_WIG = 1.0
WARMUP = 60
R_FLOOR = 0.1
DEFAULT_RISK_RATIO = 5.0  # WIG daily σ / TBSP daily σ, before the lake has a year of both


@dataclass
class Betas:
    """Kalman outputs on a stocks × sessions grid (NaN where not reported)."""

    beta: FloatArray  # stocks × sessions × factors
    resid: FloatArray  # innovation v[t]
    resid_var: FloatArray  # its predictive variance S[t]
    q: dict[int, float]  # chosen q per calendar year


def _shift(a: FloatArray) -> FloatArray:
    """Value known at t−1, aligned on t (NaN at t = 0)."""
    out = np.full_like(a, np.nan)
    out[..., 1:] = a[..., :-1]
    return out


def _risk_ratio(f: FloatArray, lo: int, hi: int) -> float:
    w = f[lo:hi]
    ok = np.isfinite(w).all(axis=1)
    if ok.sum() < 250:
        return DEFAULT_RISK_RATIO
    sd = w[ok].std(axis=0)
    return float(sd[0] / sd[1]) if sd[1] > 0 else DEFAULT_RISK_RATIO


@dataclass
class _State:
    """Filter state for every stock and every q being run."""

    b: FloatArray  # stocks × q × factors
    p: FloatArray  # stocks × q × factors × factors
    nobs: IntArray  # returns seen since the last (re)start, per stock


def _prior(n: int, g: int, ratio: float) -> tuple[_State, FloatArray]:
    p0 = np.diag([PRIOR_SD_WIG**2, (PRIOR_SD_WIG * ratio) ** 2])
    b = np.broadcast_to(PRIOR, (n, g, len(PRIOR))).copy()
    p = np.broadcast_to(p0, (n, g, *p0.shape)).copy()
    return _State(b, p, np.zeros(n, dtype=np.int64)), p0


def _run(
    ret: FloatArray,
    h_pred: FloatArray,
    f: FloatArray,
    hf_pred: FloatArray,
    seg: IntArray,
    qs: FloatArray,
    ratio: float,
    lo: int,
    hi: int,
    state: _State | None = None,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, _State]:
    """Filter sessions ``lo .. hi-1`` for every q in ``qs`` at once.

    Starts from ``state`` (or the prior) and returns betas, innovations and their variances
    (stocks × q × sessions), the pooled predictive log-likelihood per q, and the end state.
    """
    n, k, g = ret.shape[0], f.shape[1], len(qs)
    span = hi - lo
    beta_out = np.full((n, g, span, k), np.nan)
    v_out = np.full((n, g, span), np.nan)
    s_out = np.full((n, g, span), np.nan)
    ll = np.zeros(g)
    q_sd = np.stack([qs, qs * ratio], axis=1)  # g × k
    q_mat = np.zeros((g, k, k))
    q_mat[:, range(k), range(k)] = q_sd**2
    fresh, p0 = _prior(n, g, ratio)
    st = state or fresh
    b, p, nobs = st.b.copy(), st.p.copy(), st.nobs.copy()
    for j, t in enumerate(range(lo, hi)):
        if t > 0 and seg[t] != seg[t - 1]:
            b[:] = PRIOR
            p[:] = p0
            nobs[:] = 0
        p = p + q_mat[None]
        ft = f[t]
        hf = hf_pred[t]
        if not (np.isfinite(ft).all() and np.isfinite(hf).all()):
            continue
        hi_ = h_pred[:, t]
        r = ret[:, t]
        known = np.isfinite(r) & np.isfinite(hi_)
        hi0 = np.where(known, hi_, 1.0)[:, None]
        resid_var = np.maximum(hi0 - (b**2 * hf).sum(axis=2), R_FLOOR * hi0)
        fp = p @ ft  # n × g × k
        s = fp @ ft + resid_var
        v = np.where(known, r, 0.0)[:, None] - b @ ft
        gain = fp / s[..., None]
        kk = known[:, None, None]
        b = np.where(kk, b + gain * v[..., None], b)
        p = np.where(kk[..., None], p - gain[..., :, None] * fp[..., None, :], p)
        nobs += known
        rep = known & (nobs > WARMUP)
        ll -= np.where(rep[:, None], 0.5 * (np.log(s) + v**2 / s), 0.0).sum(axis=0)
        r3 = rep[:, None]
        beta_out[:, :, j] = np.where(r3[..., None], b, np.nan)
        v_out[:, :, j] = np.where(r3, v, np.nan)
        s_out[:, :, j] = np.where(r3, s, np.nan)
    return beta_out, v_out, s_out, ll, _State(b, p, nobs)


def kalman_betas(
    ret: FloatArray,
    h_next: FloatArray,
    factors: FloatArray,
    factor_h_next: FloatArray,
    seg: IntArray,
    cal: list[date],
) -> Betas:
    """Point-in-time betas, residual returns and surprises (see module docstring).

    ``ret``: stocks × sessions; ``h_next``: their ``vol.Sigma.h_next``; ``factors``:
    sessions × factors (WIG, TBSP); ``factor_h_next``: the factors' ``h_next``, same shape.

    One filter runs through the whole calendar and carries its state from year to year;
    only q (and the TBSP scale) change at a year's first session. Choosing q runs every
    candidate over the training window from the prior; that run is discarded.
    """
    n, t_n = ret.shape
    k = factors.shape[1]
    h_pred = _shift(h_next)
    hf_pred = _shift(factor_h_next.T).T
    beta = np.full((n, t_n, k), np.nan)
    resid = np.full((n, t_n), np.nan)
    resid_var = np.full((n, t_n), np.nan)
    chosen: dict[int, float] = {}
    qs = np.array(Q_GRID)
    state: _State | None = None
    for start, end in year_starts(cal):
        lo = max(0, start - TRAIN)
        ratio = _risk_ratio(factors, lo, start)
        q = Q_DEFAULT
        if start > lo:
            *_, ll, _ = _run(ret, h_pred, factors, hf_pred, seg, qs, ratio, lo, start)
            if np.any(ll != 0):
                q = float(qs[int(np.argmax(ll))])
        chosen[cal[start].year] = q
        b, v, s, _, state = _run(
            ret, h_pred, factors, hf_pred, seg, np.array([q]), ratio, start, end, state
        )
        beta[:, start:end] = b[:, 0]
        resid[:, start:end] = v[:, 0]
        resid_var[:, start:end] = s[:, 0]
    return Betas(beta, resid, resid_var, chosen)
