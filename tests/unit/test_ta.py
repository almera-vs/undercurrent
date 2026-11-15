"""Chart indicators: hand-computed values, warm-up, and restarting after a hole."""

import math

import numpy as np
import pytest

from undercurrent import ta

C = np.array([10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0])


def test_sma_and_ema_by_hand() -> None:
    s = ta.sma(C, 3)
    assert np.isnan(s[:2]).all()
    assert s[2] == pytest.approx(11.0) and s[7] == pytest.approx(14.0)
    e = ta.ema(C, 3)  # α = 0.5, started from the SMA of the first three closes
    assert np.isnan(e[:2]).all() and e[2] == pytest.approx(11.0)
    assert e[3] == pytest.approx(0.5 * 11 + 0.5 * 11.0)
    assert e[4] == pytest.approx(0.5 * 13 + 0.5 * 11.0)


def test_rsi_by_hand() -> None:
    # changes +1 +1 -1 +2: first averages over 3 changes: gain 2/3, loss 1/3
    r = ta.rsi(C[:5], 3)
    assert np.isnan(r[:3]).all()
    assert r[3] == pytest.approx(100 - 100 / (1 + 2.0))
    # Wilder's step: gain (2/3·2 + 2)/3, loss (1/3·2 + 0)/3
    g, lo = (2 / 3 * 2 + 2) / 3, (1 / 3 * 2) / 3
    assert r[4] == pytest.approx(100 - 100 / (1 + g / lo))
    assert ta.rsi(np.arange(1.0, 20.0), 14)[-1] == 100.0  # no losses
    assert ta.rsi(np.full(20, 5.0), 14)[-1] == 50.0  # no moves


def test_macd_and_bollinger_shapes() -> None:
    x = np.linspace(10, 20, 60)
    line, sig, hist = ta.macd(x)
    assert np.isnan(line[:25]).all() and np.isfinite(line[25])
    assert np.isnan(sig[:33]).all() and np.isfinite(sig[33])
    assert hist[40] == pytest.approx(line[40] - sig[40])
    lo, mid, hi = ta.bollinger(x)
    w = x[40:60]
    assert mid[59] == pytest.approx(w.mean())
    assert hi[59] - mid[59] == pytest.approx(2 * w.std())
    assert mid[59] - lo[59] == pytest.approx(2 * w.std())


def test_atr_by_hand() -> None:
    h = np.array([11.0, 12.0, 12.5, 13.0])
    lo = np.array([9.0, 10.5, 11.0, 10.0])
    c = np.array([10.0, 11.5, 12.0, 12.5])
    # true ranges: 2 (h − l), max(1.5, 2, 0.5) = 2, max(1.5, 1, 0.5) = 1.5, max(3, 1, 2) = 3
    a = ta.atr(h, lo, c, 2)
    assert math.isnan(a[0]) and a[1] == pytest.approx(2.0)
    assert a[2] == pytest.approx((2.0 + 1.5) / 2)
    assert a[3] == pytest.approx((1.75 + 3.0) / 2)


def test_52_week_high_and_low_need_a_full_year() -> None:
    x = np.arange(1.0, 301.0)
    hi, lo = ta.high_low(x + 1, x - 1)
    assert np.isnan(hi[:249]).all()
    assert hi[249] == pytest.approx(251.0) and lo[249] == pytest.approx(0.0)
    assert hi[299] == pytest.approx(301.0) and lo[299] == pytest.approx(50.0)


def test_every_indicator_restarts_after_a_hole() -> None:
    seg = np.array([0] * 4 + [1] * 4)
    s = ta.sma(C, 3, seg)
    assert np.isnan(s[4:6]).all() and s[6] == pytest.approx((13 + 14 + 13) / 3)
    assert np.isnan(ta.rsi(C, 3, seg)[4:7]).all()
    assert np.isnan(ta.ema(C, 3, seg)[4:6]).all()
    a = ta.atr(C + 1, C - 1, C, 2, seg)
    assert a[5] == pytest.approx(2.0)  # the new segment's first range is just h − l


def test_missing_high_and_low_fall_back_to_the_close() -> None:
    h, lo = ta.fill_range(np.array([np.nan, 0.0, 12.0]), np.array([9.0, np.nan, 11.5]), C[:3])
    assert h.tolist() == [10.0, 11.0, 12.0]
    assert lo.tolist() == [9.0, 11.0, 11.5]


def test_adx_of_a_steady_rise_is_100() -> None:
    c = np.arange(1.0, 61.0)
    adx, pdi, mdi = ta.adx(c + 0.5, c - 0.5, c, 14)
    assert np.isnan(adx[:27]).all() and adx[27] == pytest.approx(100.0)  # 2n − 1 sessions
    assert pdi[14] > 0 and mdi[14] == pytest.approx(0.0)
    assert np.isnan(pdi[:14]).all()


def test_adx_of_a_zigzag_is_low() -> None:
    c = np.tile([10.0, 11.0], 40)
    adx, _, _ = ta.adx(c + 0.2, c - 0.2, c, 14)
    assert adx[-1] < 20


def test_bollinger_width_and_relative_turnover() -> None:
    x = np.linspace(10, 20, 60)
    w = ta.bollinger_width(x)
    lo, mid, hi = ta.bollinger(x)
    assert w[-1] == pytest.approx((hi[-1] - lo[-1]) / mid[-1])
    tv = np.full(30, 100.0)
    tv[-1] = 250.0
    r = ta.relative(tv, 20)
    assert np.isnan(r[:20]).all() and r[-1] == pytest.approx(2.5) and r[20] == pytest.approx(1.0)
    assert ta.rolling_min(np.array([3.0, 1.0, 2.0, 5.0]), 2).tolist()[1:] == [1.0, 1.0, 2.0]
