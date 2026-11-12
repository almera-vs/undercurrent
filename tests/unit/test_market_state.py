"""Market state (L2): GARCH σ, Kalman betas, regime, liquidity, spreads, costs."""

import hashlib
import io
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from tests.study_fixtures import known, sessions
from undercurrent import beta, market_state, regime, study, vol


def _garch(n: int, t_n: int, a: float, b: float, v: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    r = np.zeros((n, t_n))
    h = np.full(n, v)
    for t in range(t_n):
        r[:, t] = np.sqrt(h) * rng.standard_normal(n)
        h = v * (1 - a - b) + a * r[:, t] ** 2 + b * h
    return r


# --------------------------------------------------------------------------- GARCH


def test_garch_grid_recovers_simulated_parameters():
    r = _garch(80, 1500, 0.10, 0.85, 4e-4, seed=1)
    p = vol.fit_garch(r, np.zeros(1500, dtype=np.int64), 0, 1500)
    assert np.median(p.alpha) == pytest.approx(0.10, abs=0.02)
    assert np.median(p.alpha + p.beta) == pytest.approx(0.95, abs=0.03)
    assert np.median(p.target) == pytest.approx(4e-4, rel=0.15)


def test_sigma_is_point_in_time_under_truncation():
    cal = sessions(1100, start=date(2015, 1, 5))
    seg = study.calendar_segments(cal)
    r = _garch(10, len(cal), 0.08, 0.9, 4e-4, seed=2)
    full = vol.sigma_forecasts(r, seg, cal)
    for cut in (300, 640, 1000):
        part = vol.sigma_forecasts(r[:, :cut], seg[:cut], cal[:cut])
        np.testing.assert_array_equal(part.h_next, full.h_next[:, :cut])
        np.testing.assert_array_equal(part.source, full.source[:, :cut])


def test_short_history_falls_back_to_ewma_then_garch():
    cal = sessions(800, start=date(2015, 1, 5))
    seg = study.calendar_segments(cal)
    r = _garch(1, len(cal), 0.08, 0.9, 4e-4, seed=3)
    s = vol.sigma_forecasts(r, seg, cal)
    first_year = [t for t, d in enumerate(cal) if d.year == 2015]
    assert (s.source[0, : vol.EWMA_WARMUP - 1] == vol.SOURCE_NONE).all()
    assert (s.source[0, vol.EWMA_WARMUP : first_year[-1] + 1] == vol.SOURCE_EWMA).all()
    later = [t for t, d in enumerate(cal) if d.year == 2016]
    assert (s.source[0, later] == vol.SOURCE_GARCH).all()


def test_missing_returns_and_holes():
    cal = sessions(600, start=date(2015, 1, 5))
    cal = cal[:400] + [d + timedelta(days=10) for d in cal[400:]]  # a hole in 2016
    seg = study.calendar_segments(cal)
    assert seg[399] != seg[400] and cal[400].year == 2016
    r = _garch(1, len(cal), 0.08, 0.9, 4e-4, seed=4)
    r[0, 300:310] = np.nan  # a stock that did not trade for two weeks
    s = vol.sigma_forecasts(r, seg, cal)
    assert np.isfinite(s.h_next[0, 300:310]).all()
    assert (s.source[0, 270:] == vol.SOURCE_GARCH).all()
    # 2016's parameters are fixed before 2016, so a crash just before the hole changes
    # nothing after it: the filter restarts from the long-run variance...
    in_2016 = [t for t in range(400, len(cal)) if cal[t].year == 2016]
    shocked = r.copy()
    shocked[0, 390:400] = 0.3
    after = vol.sigma_forecasts(shocked, seg, cal)
    np.testing.assert_array_equal(after.h_next[0, in_2016], s.h_next[0, in_2016])
    assert after.h_next[0, 399] > 10 * s.h_next[0, 399]  # (it did see the crash before)
    # ...and the EWMA restarts with a new warm-up
    ew = vol.ewma_path(r, seg)
    assert not np.isfinite(ew[0, 400 : 400 + vol.EWMA_WARMUP - 1]).any()


def test_variance_floor_tames_a_stale_price():
    cal = sessions(400, start=date(2015, 1, 5))
    seg = study.calendar_segments(cal)
    r = np.zeros((1, len(cal)))
    s = vol.sigma_forecasts(r, seg, cal)
    ok = np.isfinite(s.h_next)
    assert (s.h_next[ok] >= vol.VAR_FLOOR).all()
    idx = vol.sigma_forecasts(np.full((1, len(cal)), 1e-4), seg, cal, vol.INDEX_VAR_FLOOR)
    assert np.nanmax(idx.h_next) < vol.VAR_FLOOR


def test_horizon_variance_mean_reverts_to_target():
    s = vol.Sigma(
        h_next=np.array([[4e-4, 1e-4]]),
        source=np.array([[1, 1]], dtype=np.int8),
        persistence=np.array([[0.9, 0.9]]),
        target=np.array([[1e-4, 4e-4]]),
    )
    hv = s.horizon_var(20)
    assert hv[0, 0] < 20 * 4e-4 and hv[0, 0] > 20 * 1e-4  # high now, falls toward target
    assert hv[0, 1] > 20 * 1e-4 and hv[0, 1] < 20 * 4e-4  # low now, rises toward target
    ewma = vol.Sigma(
        np.array([[2e-4]]), np.array([[2]], dtype=np.int8), np.array([[1.0]]), np.array([[2e-4]])
    )
    assert ewma.horizon_var(20)[0, 0] == pytest.approx(20 * 2e-4)


# --------------------------------------------------------------------------- Kalman


def _beta_market(t_n: int = 1100, seed: int = 5):
    rng = np.random.default_rng(seed)
    cal = sessions(t_n, start=date(2015, 1, 5))
    seg = study.calendar_segments(cal)
    wig = 0.01 * rng.standard_normal(t_n)
    tb = 0.002 * rng.standard_normal(t_n)
    b_true = np.where(np.arange(t_n) < 700, 0.5, 1.5)
    r = b_true * wig - 2.0 * tb + 0.015 * rng.standard_normal((40, t_n))
    f = np.stack([wig, tb], axis=1)
    return cal, seg, r, f


def test_kalman_tracks_a_beta_change_and_the_bond_beta():
    cal, seg, r, f = _beta_market()
    s = vol.sigma_forecasts(r, seg, cal)
    fs = vol.sigma_forecasts(f.T, seg, cal, vol.INDEX_VAR_FLOOR)
    b = beta.kalman_betas(r, s.h_next, f, fs.h_next.T, seg, cal)
    before = np.nanmean(b.beta[:, 690, 0])
    assert before == pytest.approx(0.5, abs=0.1)
    # trained on a constant beta, the filter drifts slowly: most of the way in ~400 sessions,
    # without a jump when the year (and q) changes
    path = [np.nanmean(b.beta[:, t, 0]) for t in (760, 900, 1090)]
    assert path[0] < path[1] < path[2]
    assert 1.2 < path[-1] < 1.6
    for start in (t for t in range(1, 1100) if cal[t].year != cal[t - 1].year):
        step = np.nanmean(np.abs(b.beta[:, start, 0] - b.beta[:, start - 1, 0]))
        assert step < 0.05
    assert np.nanmean(b.beta[:, 1090, 1]) == pytest.approx(-2.0, abs=0.3)
    z = b.resid / np.sqrt(b.resid_var)
    assert np.nanstd(z) == pytest.approx(1.0, abs=0.1)
    assert set(b.q) == {d.year for d in cal}


def test_kalman_is_point_in_time_and_warms_up():
    cal, seg, r, f = _beta_market(900)
    fs = vol.sigma_forecasts(f.T, seg, cal, vol.INDEX_VAR_FLOOR)

    def run(cut: int) -> beta.Betas:
        s = vol.sigma_forecasts(r[:, :cut], seg[:cut], cal[:cut])
        return beta.kalman_betas(
            r[:, :cut], s.h_next, f[:cut], fs.h_next.T[:cut], seg[:cut], cal[:cut]
        )

    full, part = run(900), run(600)
    np.testing.assert_allclose(part.beta, full.beta[:, :600], rtol=0, atol=0)
    np.testing.assert_allclose(part.resid, full.resid[:, :600], rtol=0, atol=0)
    assert not np.isfinite(full.beta[:, : beta.WARMUP]).any()


# --------------------------------------------------------------------------- regime


def _regime_series(seed: int = 6):
    rng = np.random.default_rng(seed)
    cal = sessions(1500, start=date(2015, 1, 5))
    seg = study.calendar_segments(cal)
    stress = np.zeros(len(cal), dtype=bool)
    for a, b in ((300, 380), (900, 1000), (1300, 1360)):
        stress[a:b] = True
    x = (np.where(stress, 0.025, 0.008) * rng.standard_normal(len(cal)))[:, None]
    return cal, seg, x, stress


def test_regime_finds_planted_stress_with_filtered_probabilities():
    cal, seg, x, stress = _regime_series()
    p = regime.stress_probability(x, seg, cal)
    ok = np.isfinite(p)
    assert p[ok & stress].mean() > 0.7
    assert p[ok & ~stress].mean() < 0.15
    # filtered, not smoothed: cutting the data leaves earlier values unchanged
    part = regime.stress_probability(x[:1200], seg[:1200], cal[:1200])
    np.testing.assert_array_equal(part, p[:1200])


def test_regime_needs_history_and_persistent_states():
    cal, seg, x, _ = _regime_series()
    p = regime.stress_probability(x, seg, cal)
    assert not np.isfinite(p[: next(t for t, d in enumerate(cal) if d.year == 2016)]).any()
    rng = np.random.default_rng(7)
    iid = rng.standard_t(3, size=(600, 1)) * 0.01  # fat tails, no regimes
    m = regime.fit_hmm(iid, np.zeros(600, dtype=np.int64))
    assert m is None or np.diag(m.trans).min() >= regime.MIN_PERSISTENCE


# --------------------------------------------------------------------------- the table


def _inputs(n_stocks: int = 12, n_sessions: int = 700, seed: int = 8, spread: float = 0.01):
    rng = np.random.default_rng(seed)
    cal = sessions(n_sessions, start=date(2015, 1, 5))
    wig = rng.normal(0.0002, 0.01, n_sessions)
    tb = rng.normal(0.0001, 0.002, n_sessions)
    rows = []
    for i in range(n_stocks):
        thin = i == 0
        r = 0.8 * wig + rng.normal(0, 0.015, n_sessions)
        eff = 20 * np.cumprod(1 + r)
        side = rng.choice([-1.0, 1.0], n_sessions)
        close = eff * (1 + side * spread / 2)
        rng_ = 0.01 * eff
        value = 2_000.0 if thin else 400_000.0 * (1 + i)
        for j, d in enumerate(cal):
            rows.append(
                {
                    "isin": f"PLSTK{i:07d}",
                    "trade_date": d,
                    "market": "GPW",
                    "close": close[j],
                    "adj_ret": None if j == 0 else close[j] / close[j - 1] - 1,
                    "value_pln": value,
                    "volume": value / close[j],
                    "high": eff[j] + rng_[j] + eff[j] * spread / 2,
                    "low": eff[j] - rng_[j] - eff[j] * spread / 2,
                    "ca": 0,
                    "known_at": known(d),
                }
            )
    prices = pl.DataFrame(rows)
    idx = []
    for name, ret in (("WIG", wig), ("TBSP.INDEX", tb)):
        for j, d in enumerate(cal):
            idx.append(
                {
                    "name": name,
                    "trade_date": d,
                    "ret": None if j == 0 else float(ret[j]),
                    "known_at": known(d),
                }
            )
    sess = pl.DataFrame({"trade_date": cal, "known_at": [known(d) for d in cal]})
    return prices, pl.DataFrame(idx), sess, cal


def _frame(prices, idx, sess, cal, cut: int | None = None) -> pl.DataFrame:
    as_of = known(cal[(cut or len(cal)) - 1])
    return market_state.to_frame(
        market_state.compute(market_state.cut_inputs(prices, idx, sess, as_of))
    )


def test_table_is_point_in_time_and_deterministic():
    prices, idx, sess, cal = _inputs()
    full = _frame(prices, idx, sess, cal)
    part = _frame(prices, idx, sess, cal, cut=450)
    head = full.filter(pl.col("trade_date") <= cal[449])
    assert part.equals(head)

    def sha(df: pl.DataFrame) -> str:
        buf = io.BytesIO()
        df.write_parquet(buf)
        return hashlib.sha256(buf.getvalue()).hexdigest()

    assert sha(full) == sha(_frame(prices, idx, sess, cal))


def test_table_columns_spread_cost_and_tiers():
    prices, idx, sess, cal = _inputs(spread=0.01)
    df = _frame(prices, idx, sess, cal)
    last = df.filter(pl.col("trade_date") == cal[-1]).sort("isin")
    assert last.height == 12
    liquid = last.filter(pl.col("liquidity_tier") == "liquid")
    assert liquid.height == 11
    assert last.filter(pl.col("isin") == "PLSTK0000000")["liquidity_tier"][0] == "thin"
    # Abdi–Ranaldo recovers the planted 1 % spread
    assert liquid["spread"].median() == pytest.approx(0.01, abs=0.004)
    assert (liquid["spread_source"] == "stock").all()
    # cost: spread + commission both ways + impact, falling with turnover
    c = liquid.sort("median_turnover")["cost"].to_list()
    assert c[0] > c[-1] > 0.01 + 2 * market_state.COMMISSION
    assert last["cost"].max() <= market_state.MAX_COST
    assert last["sigma"].is_not_null().all()
    assert last["beta_wig"].drop_nulls().median() == pytest.approx(0.8, abs=0.2)
    assert set(last["sigma_source"].to_list()) <= {"garch", "ewma"}


def test_table_refuses_rows_from_the_future():
    prices, idx, sess, cal = _inputs(n_stocks=2, n_sessions=60)
    inputs = market_state.cut_inputs(prices, idx, sess, known(cal[-1]))
    leaked = market_state.StateInputs(
        as_of=known(cal[30]), prices=inputs.prices, indices=inputs.indices, sessions=sess
    )
    with pytest.raises(study.LookAheadError):
        market_state.build_grid(leaked)


def test_market_spread_fallback_for_a_stock_with_few_trades():
    g_cost, used, src = market_state.costs(
        np.array([[0.02, 0.02]]),
        np.array([[50_000.0, 0.0]]),
        np.array([[np.nan, np.nan]]),
        ["NC"],
    )
    assert src[0, 0] == "market" and used[0, 0] == market_state.MARKET_SPREAD["NC"]
    assert g_cost[0, 1] == market_state.MAX_COST  # no turnover: untradable
