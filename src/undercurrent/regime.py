"""Market regime for the market state: a two-state Gaussian hidden Markov model.

Observations per session: WIG's daily return (the code takes any number of columns, with a
diagonal covariance per state). The state with the larger WIG variance is called
**stress**; the output is its *filtered* probability, ``P(stress at t | sessions ≤ t)``.
Smoothed probabilities would use later sessions and are never produced.

Adding the size spread (sWIG80 minus WIG20) as a second column was tried on 2015–2017: EM
then split days, not periods (the chance of staying in a state was about 0.5), so it is not
used. A fit whose states do not persist (``MIN_PERSISTENCE``) is rejected the same way.

* **Fitted like the other state models.** Parameters for calendar year Y come from EM
  (Baum–Welch) on the ``TRAIN`` sessions before Y, a fixed ``EM_ITERATIONS`` iterations from
  a deterministic start: the calmest 80 % of sessions (by absolute WIG return) versus the
  rest. The filter then runs from the start of that window through Y with them frozen.
  A year with fewer than ``MIN_FIT`` training sessions gets no regime.
* **Holes and gaps.** Each calendar segment is its own sequence: the filter restarts from
  the stationary distribution after a hole. A session with a missing observation only
  moves the probabilities through the transition matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from undercurrent.vol import TRAIN, year_starts

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

MIN_FIT = 250
EM_ITERATIONS = 50
CALM_SHARE = 0.8
VAR_FLOOR = 1e-10
MIN_PERSISTENCE = 0.8  # each state's chance of lasting another session


@dataclass(frozen=True)
class HMM:
    """Two-state Gaussian HMM with diagonal covariances; state 1 is stress."""

    start: FloatArray  # 2
    trans: FloatArray  # 2 × 2, rows sum to 1
    mean: FloatArray  # 2 × d
    var: FloatArray  # 2 × d


def _density(x: FloatArray, m: HMM) -> FloatArray:
    """Sessions × 2 likelihoods; 1 for both states where the session has no observation."""
    ok = np.isfinite(x).all(axis=1)
    x0 = np.where(ok[:, None], x, 0.0)
    d = x0[:, None, :] - m.mean[None]
    logp = -0.5 * ((d**2) / m.var[None] + np.log(2 * np.pi * m.var[None])).sum(axis=2)
    logp -= logp.max(axis=1, keepdims=True)
    out = np.exp(logp)
    out[~ok] = 1.0
    return np.asarray(out, dtype=np.float64)


def _stationary(trans: FloatArray) -> FloatArray:
    a, b = trans[0, 1], trans[1, 0]
    if a + b <= 0:
        return np.array([0.5, 0.5])
    return np.array([b / (a + b), a / (a + b)])


def _segments(seg: IntArray) -> list[tuple[int, int]]:
    cuts = [0, *[t for t in range(1, len(seg)) if seg[t] != seg[t - 1]], len(seg)]
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1) if cuts[k + 1] > cuts[k]]


def _initial(x: FloatArray) -> HMM:
    ok = np.isfinite(x).all(axis=1)
    xs = x[ok]
    thr = np.quantile(np.abs(xs[:, 0]), CALM_SHARE)
    calm = np.abs(xs[:, 0]) <= thr
    mean = np.stack([xs[calm].mean(axis=0), xs[~calm].mean(axis=0)])
    var = np.maximum(np.stack([xs[calm].var(axis=0), xs[~calm].var(axis=0)]), VAR_FLOOR)
    trans = np.array([[0.98, 0.02], [0.08, 0.92]])
    return HMM(_stationary(trans), trans, mean, var)


def fit_hmm(x: FloatArray, seg: IntArray) -> HMM | None:
    """Baum–Welch on observations ``x`` (sessions × d), each segment its own sequence."""
    ok = np.isfinite(x).all(axis=1)
    if ok.sum() < MIN_FIT:
        return None
    m = _initial(x)
    for _ in range(EM_ITERATIONS):
        dens = _density(x, m)
        gam_sum = np.zeros(2)
        xi_sum = np.zeros((2, 2))
        w_sum = np.zeros(2)
        mx = np.zeros_like(m.mean)
        mx2 = np.zeros_like(m.var)
        start_acc = np.zeros(2)
        for a, b in _segments(seg):
            d = dens[a:b]
            t_n = b - a
            alpha = np.zeros((t_n, 2))
            c = np.zeros(t_n)
            alpha[0] = m.start * d[0]
            c[0] = alpha[0].sum()
            alpha[0] /= c[0]
            for t in range(1, t_n):
                alpha[t] = (alpha[t - 1] @ m.trans) * d[t]
                c[t] = alpha[t].sum()
                alpha[t] /= c[t]
            beta = np.ones((t_n, 2))
            for t in range(t_n - 2, -1, -1):
                beta[t] = (m.trans @ (d[t + 1] * beta[t + 1])) / c[t + 1]
            gam = alpha * beta
            gam /= gam.sum(axis=1, keepdims=True)
            start_acc += gam[0]
            if t_n > 1:
                xi = (
                    alpha[:-1, :, None]
                    * m.trans[None]
                    * (d[1:] * beta[1:])[:, None, :]
                    / c[1:, None, None]
                )
                xi_sum += xi.sum(axis=0)
                gam_sum += gam[:-1].sum(axis=0)
            okw = ok[a:b]
            g = gam[okw]
            xo = x[a:b][okw]
            w_sum += g.sum(axis=0)
            mx += g.T @ xo
            mx2 += g.T @ (xo**2)
        trans = xi_sum / np.maximum(gam_sum[:, None], 1e-300)
        trans /= trans.sum(axis=1, keepdims=True)
        mean = mx / w_sum[:, None]
        var = np.maximum(mx2 / w_sum[:, None] - mean**2, VAR_FLOOR)
        m = HMM(start_acc / start_acc.sum(), trans, mean, var)
    if m.var[0, 0] > m.var[1, 0]:  # state 1 must be the stress state
        perm = [1, 0]
        m = HMM(m.start[perm], m.trans[np.ix_(perm, perm)], m.mean[perm], m.var[perm])
    if not np.isfinite(m.trans).all() or np.diag(m.trans).min() < MIN_PERSISTENCE:
        return None
    return m


def filtered_stress(x: FloatArray, seg: IntArray, m: HMM, lo: int, hi: int) -> FloatArray:
    """``P(stress | sessions ≤ t)`` for sessions ``lo .. hi-1``."""
    dens = _density(x[lo:hi], m)
    stat = _stationary(m.trans)
    out = np.full(hi - lo, np.nan)
    prob = stat.copy()
    for j, t in enumerate(range(lo, hi)):
        prior = stat if (j == 0 or seg[t] != seg[t - 1]) else prob @ m.trans
        post = prior * dens[j]
        prob = post / post.sum()
        out[j] = prob[1]
    return out


def stress_probability(x: FloatArray, seg: IntArray, cal: list[date]) -> FloatArray:
    """Point-in-time stress probability for every session (NaN without a fitted model)."""
    out = np.full(len(cal), np.nan)
    for start, end in year_starts(cal):
        lo = max(0, start - TRAIN)
        m = fit_hmm(x[lo:start], seg[lo:start]) if start > lo else None
        if m is None:
            continue
        out[start:end] = filtered_stress(x, seg, m, lo, end)[start - lo :]
    return out
