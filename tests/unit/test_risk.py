"""Risk figures: unit-variance t quantiles, ν from the data, the backtest, drawdown."""

import math

import numpy as np
import pytest
from scipy import stats as sps

from undercurrent import risk


def test_quantiles_have_unit_variance() -> None:
    for nu in (3.0, 5.0, 10.0):
        # the scaled t has standard deviation 1, so its VaR is below the plain t's
        sd = math.sqrt(nu / (nu - 2))
        assert risk.quantile(0.01, nu) == pytest.approx(sps.t.ppf(0.01, nu) / sd)
        draws = sps.t.rvs(nu, size=400_000, random_state=1) / sd
        assert np.std(draws) == pytest.approx(1.0, rel=0.05)
        assert np.mean(draws < risk.quantile(0.05, nu)) == pytest.approx(0.05, abs=0.002)
        tail = draws[draws < risk.quantile(0.025, nu)]
        assert -tail.mean() == pytest.approx(risk.shortfall(0.975, nu), rel=0.03)
    assert risk.quantile(0.05, None) == pytest.approx(sps.norm.ppf(0.05))
    assert risk.shortfall(0.975, None) == pytest.approx(2.3378, rel=1e-3)


def test_nu_is_recovered_from_fat_tailed_data() -> None:
    rng = np.random.default_rng(2)
    z = rng.standard_t(4, size=3000) / math.sqrt(4 / 2)
    assert risk.fit_nu(z) in (4.0, 5.0)
    assert risk.fit_nu(rng.normal(size=3000)) == 30.0
    assert risk.fit_nu(z[:50]) is None  # too few


def test_kupiec() -> None:
    assert risk.kupiec(250, 12, 0.05) > 0.5  # about the expected 12.5
    assert risk.kupiec(250, 30, 0.05) < 0.001
    assert risk.kupiec(250, 0, 0.01) == pytest.approx(sps.chi2.sf(-2 * 250 * math.log(0.99), 1))


def test_profile_backtest_and_drawdown() -> None:
    rng = np.random.default_rng(3)
    n = 1200
    sigma = np.full(n, 0.02)
    ret = sigma * rng.standard_t(5, size=n) / math.sqrt(5 / 3)
    ret[0] = np.nan
    c = 100 * np.cumprod(1 + np.nan_to_num(ret))
    seg = np.zeros(n, dtype=np.int64)
    p = risk.profile(ret, sigma, c, seg)
    assert p is not None
    assert p["nu"] is not None and p["nu"] <= 15  # 500 draws of a t(5): fat-tailed, ν noisy
    assert p["var99"] > p["var95"] > 0 and p["es975"] > p["var95"]
    bt = p["backtest"]
    assert bt["n"] == 250 and bt["p95"] is not None and bt["p95"] > 0.01
    assert p["drawdown"] == pytest.approx(c[-1] / c.max() - 1)
    assert -1 < p["max_drawdown_year"] <= 0
    assert p["vol_percentile"] == 1.0  # σ never changed
    assert risk.profile(ret, np.full(n, np.nan), c, seg) is None
