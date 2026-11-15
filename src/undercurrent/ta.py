"""Chart indicators: the classic technical-analysis set, on one stock's adjusted prices.

The site's stock pages and screener show these (``stock_export.py``); the browser has the same
formulas in ``site/static/js/indicators.js`` for when a reader changes a parameter, and a CI
check runs both on one fixture so they cannot drift apart. This module is the reference.

Conventions, the same in both implementations:

* Inputs are one stock's sessions in order, with a segment number per session
  (``seg``): the segment changes after a hole in the lake (``study.calendar_segments``). Every
  indicator restarts at a new segment, as the study's windows do: sessions on the far side of
  a hole are not neighbours.
* An indicator is NaN until its window is full inside the segment.
* **SMA** n: the mean of the last n closes. **EMA** n: ``α = 2 / (n + 1)``, started from the
  SMA of the first n closes.
* **RSI** n (Wilder): the first average gain and loss are the means of the first n changes,
  then ``avg ← (avg·(n − 1) + x) / n``. RSI = 100 − 100 / (1 + gain / loss); 100 when there is
  no loss, 50 when there is neither.
* **MACD** (12, 26, 9): EMA12 − EMA26; the signal line is the EMA9 of the MACD, started from
  the SMA of its first 9 values; the histogram is MACD − signal.
* **Bollinger** (20, 2): SMA20 ± 2 standard deviations of the same 20 closes (population).
* **ATR** n (Wilder): true range ``max(h − l, |h − c₋₁|, |l − c₋₁|)``, ``h − l`` on a segment's
  first session; the first ATR is the mean of the first n true ranges.
* **52-week** high and low: the highest high and lowest low of the last 250 sessions,
  including today; NaN with fewer than 250 sessions in the segment.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
from scipy.signal import lfilter

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

YEAR = 250  # sessions in the 52-week window
RSI_N = 14
ATR_N = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_N, BB_K = 20, 2.0


def _segments(seg: IntArray | None, n: int) -> list[slice]:
    """Slices of consecutive sessions with the same segment number."""
    if seg is None or n == 0:
        return [slice(0, n)]
    cuts = np.nonzero(np.diff(seg))[0] + 1
    bounds = [0, *cuts.tolist(), n]
    return [slice(a, b) for a, b in itertools.pairwise(bounds)]


def _per_segment(
    f: Callable[..., FloatArray], seg: IntArray | None, *arrays: FloatArray
) -> FloatArray:
    n = len(arrays[0])
    out = np.full(n, np.nan)
    for s in _segments(seg, n):
        out[s] = f(*(a[s] for a in arrays))
    return out


def _sma(x: FloatArray, n: int) -> FloatArray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    c = np.concatenate([[0.0], np.cumsum(x)])
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def _smooth(x: FloatArray, n: int, alpha: float) -> FloatArray:
    """``y = α·x + (1 − α)·y₋₁``, started at index n − 1 from the mean of the first n."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    seed = float(np.mean(x[:n]))
    out[n - 1] = seed
    if len(x) > n:
        out[n:], _ = lfilter([alpha], [1.0, alpha - 1.0], x[n:], zi=[(1.0 - alpha) * seed])
    return out


def _ema(x: FloatArray, n: int) -> FloatArray:
    return _smooth(x, n, 2.0 / (n + 1))


def sma(close: FloatArray, n: int, seg: IntArray | None = None) -> FloatArray:
    """Simple moving average of ``n`` closes."""
    return _per_segment(lambda c: _sma(c, n), seg, close)


def ema(close: FloatArray, n: int, seg: IntArray | None = None) -> FloatArray:
    """Exponential moving average, started from the SMA of the first ``n`` closes."""
    return _per_segment(lambda c: _ema(c, n), seg, close)


def _rsi(c: FloatArray, n: int) -> FloatArray:
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    gain = _smooth(np.maximum(d, 0.0), n, 1.0 / n)
    loss = _smooth(np.maximum(-d, 0.0), n, 1.0 / n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100.0 - 100.0 / (1.0 + gain / loss)
    r = np.where(loss == 0, np.where(gain == 0, 50.0, 100.0), r)
    out[1:] = np.where(np.isfinite(gain), r, np.nan)
    return out


def rsi(close: FloatArray, n: int = RSI_N, seg: IntArray | None = None) -> FloatArray:
    """Wilder's relative strength index."""
    return _per_segment(lambda c: _rsi(c, n), seg, close)


def _macd(c: FloatArray, fast: int, slow: int, signal: int) -> tuple[FloatArray, FloatArray]:
    line = _ema(c, fast) - _ema(c, slow)
    sig = np.full(len(c), np.nan)
    ok = np.nonzero(np.isfinite(line))[0]
    if len(ok):
        sig[ok[0] :] = _ema(line[ok[0] :], signal)
    return line, sig


def macd(
    close: FloatArray,
    seg: IntArray | None = None,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """MACD line, signal line and histogram."""
    line = _per_segment(lambda c: _macd(c, fast, slow, signal)[0], seg, close)
    sig = _per_segment(lambda c: _macd(c, fast, slow, signal)[1], seg, close)
    return line, sig, line - sig


def _std(x: FloatArray, n: int) -> FloatArray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(x, n)
    out[n - 1 :] = w.std(axis=1)
    return out


def bollinger(
    close: FloatArray, seg: IntArray | None = None, n: int = BB_N, k: float = BB_K
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Lower band, middle (SMA) and upper band."""
    mid = sma(close, n, seg)
    sd = _per_segment(lambda c: _std(c, n), seg, close)
    return mid - k * sd, mid, mid + k * sd


def _atr(h: FloatArray, lo: FloatArray, c: FloatArray, n: int) -> FloatArray:
    prev = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - prev), np.abs(lo - prev)]), axis=0)
    return _smooth(tr, n, 1.0 / n)


def atr(
    high: FloatArray,
    low: FloatArray,
    close: FloatArray,
    n: int = ATR_N,
    seg: IntArray | None = None,
) -> FloatArray:
    """Wilder's average true range."""
    return _per_segment(lambda h, lo, c: _atr(h, lo, c, n), seg, high, low, close)


def _rolling(x: FloatArray, n: int, f: Callable[..., FloatArray]) -> FloatArray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1 :] = f(np.lib.stride_tricks.sliding_window_view(x, n), axis=1)
    return out


def high_low(
    high: FloatArray, low: FloatArray, seg: IntArray | None = None, n: int = YEAR
) -> tuple[FloatArray, FloatArray]:
    """The highest high and lowest low of the last ``n`` sessions (52 weeks)."""
    hi = _per_segment(lambda h: _rolling(h, n, np.max), seg, high)
    lo = _per_segment(lambda x: _rolling(x, n, np.min), seg, low)
    return hi, lo


def fill_range(
    high: FloatArray, low: FloatArray, close: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """High and low with the close where a session has none (no trade, or not published)."""
    h = np.where(np.isfinite(high) & (high > 0), high, close)
    lo = np.where(np.isfinite(low) & (low > 0), low, close)
    return np.maximum(h, close), np.minimum(lo, close)
