"""Stock of the week reading the market state: features, σ scaling, per-stock costs, variants."""

from datetime import timedelta

import numpy as np
import polars as pl
import pytest

from tests.unit.test_pick import _as_of, _known, market
from undercurrent import pick, study
from undercurrent import pick_features as pf


def _state(prices: pl.DataFrame, signal: dict[str, np.ndarray], cal, costs: dict[str, float]):
    """A market-state table: ``bond_beta`` carries ``signal``; everything else is plain."""
    rows = []
    for (isin,), _ in prices.group_by("isin", maintain_order=True):
        isin = str(isin)
        sig = signal[isin]
        for j, d in enumerate(cal):
            rows.append(
                {
                    "isin": isin,
                    "trade_date": d,
                    "sigma_20": 0.06 + 0.01 * (int(isin[-1]) % 3),
                    "beta_wig": 1.0,
                    "beta_tbsp": float(sig[j]),
                    "resid": 0.0,
                    "surprise": 0.0,
                    "cost": costs[isin],
                    "known_at": _known(d),
                }
            )
    return pl.DataFrame(rows)


@pytest.fixture(scope="module")
def informed():
    """Stocks whose excess return follows a hidden quality that changes every 60 sessions.

    Momentum sees it late; the state's ``beta_tbsp`` column sees it at once (with noise).
    """
    n, t_n = 40, 900
    rng = np.random.default_rng(21)
    prices, bench, events, cal = market(n_stocks=n, n_sessions=t_n, seed=21)
    quality = np.repeat(rng.normal(0, 1, (n, t_n // 60 + 1)), 60, axis=1)[:, :t_n]
    alpha = 0.002 * quality
    isins = sorted(set(prices["isin"].to_list()))
    # rebuild prices with the planted, time-varying excess return
    mkt = bench["wig_ret"].to_numpy()
    out = []
    for i, isin in enumerate(isins):
        r = mkt + alpha[i] + rng.normal(0, 0.012, t_n)
        price = 20 * np.cumprod(1 + r)
        for j, d in enumerate(cal):
            out.append(
                {
                    "isin": isin,
                    "trade_date": d,
                    "ticker": f"S{i:02d}",
                    "close": float(price[j]),
                    "adj_ret": float(r[j]) if j else None,
                    "value_pln": 500_000.0,
                    "ca": 0,
                    "known_at": _known(d),
                }
            )
    prices = pl.DataFrame(out)
    signal = {s: quality[i] + rng.normal(0, 0.5, t_n) for i, s in enumerate(isins)}
    costs = {s: (0.004 if i % 2 else 0.03) for i, s in enumerate(isins)}
    state = _state(prices, signal, cal, costs)
    as_of = _as_of(cal[-1])
    with_state = pick.compute(pf.cut_inputs(prices, bench, events, as_of, state))
    without = pick.compute(pf.cut_inputs(prices, bench, events, as_of))
    return {"with": with_state, "without": without, "costs": costs, "cal": cal}


def test_a_useful_state_feature_is_adopted(informed):
    ab = informed["with"]["ablation"]
    ic = dict(zip(ab["variant"], ab["ic_mean"], strict=True))
    assert ic["state_features"] > ic["base"] + 0.05
    s = informed["with"]["summary"]
    assert s["variant"] in ("state_features", "state") and s["uses_state"]
    model = informed["with"]["model"]
    weights = dict(zip(model.features, model.weights, strict=True))
    assert weights["bond_beta"] == max(weights.values())
    assert s["trials"] == len(pick.VARIANTS) * len(pick.PENALTIES) + 1


def test_without_the_state_all_variants_are_the_base_model(informed):
    ab = informed["without"]["ablation"]
    assert ab["ic_mean"].n_unique() == 1
    assert informed["without"]["summary"]["variant"] == "base"
    assert not informed["without"]["summary"]["uses_state"]
    assert informed["without"]["model"].features == pf.FEATURES


def test_every_purchase_pays_the_stocks_own_cost(informed):
    bt = informed["with"]["backtest"]
    costs = informed["costs"]
    for r in bt.filter(pl.col("switched")).iter_rows(named=True):
        assert r["cost"] == pytest.approx(costs[r["isin"]])
        if np.isfinite(r["gross"]):
            assert r["net"] == pytest.approx(r["gross"] - costs[r["isin"]])
    held = bt.filter(~pl.col("switched") & pl.col("gross").is_not_nan())
    assert (held["net"] == held["gross"]).all()
    scores = informed["with"]["scores"]
    assert (scores["expected_net"] - (scores["expected"] - scores["cost"])).abs().max() < 1e-12


def test_hysteresis_uses_the_leaders_cost():
    stocks = np.array([3, 7, 9])
    held = pick.Holding(7, 0.03)
    exp = np.array([0.04, 0.03, 0.0])
    cheap = np.array([0.005, 0.02, 0.02])
    dear = np.array([0.02, 0.02, 0.02])
    assert pick.pick_with_hysteresis(held, stocks, exp, cheap)[0].stock == 3  # +1 pp > 0.5 %
    assert pick.pick_with_hysteresis(held, stocks, exp, dear)[0].stock == 7  # +1 pp < 2 %


def test_scaled_expected_is_score_map_times_sigma():
    m = pick.Model(
        weights=np.ones(len(pf.ALL_FEATURES)),
        penalty=1.0,
        intercept=0.1,
        slope=0.5,
        train_weeks=50,
        variant=pick.VARIANTS[2],
    )
    z = np.zeros((2, len(pf.ALL_FEATURES)))
    z[1, 0] = 1.0
    scale = np.array([0.05, 0.10])
    np.testing.assert_allclose(m.expected(z, scale), [0.1 * 0.05, 0.6 * 0.10])
    flat = pick.Model(np.ones(len(pf.FEATURES)), 1.0, 0.1, 0.5, 50)
    np.testing.assert_allclose(flat.expected(z, scale), [0.1, 0.6])


def test_state_rows_from_the_future_are_cut_or_refused():
    prices, bench, events, cal = market(n_stocks=3, n_sessions=60)
    isins = sorted(set(prices["isin"].to_list()))
    state = _state(prices, {s: np.zeros(len(cal)) for s in isins}, cal, dict.fromkeys(isins, 0.01))
    inputs = pf.cut_inputs(prices, bench, events, _as_of(cal[30]), state)
    assert inputs.state is not None and inputs.state["trade_date"].max() == cal[30]
    leaked = pf.PickInputs(inputs.as_of, inputs.prices, inputs.benchmark, inputs.events, state)
    with pytest.raises(study.LookAheadError):
        pf.build_panel(leaked)


def test_live_record_keeps_the_cost_it_paid(tmp_path, informed):
    out = informed["with"]
    panel, model = out["panel"], out["model"]
    monday = next(d for d in reversed(panel.cal) if pick.is_rebalance_day(panel, d))
    live = pick.record_live(tmp_path, panel, model, monday, _as_of(monday))
    assert live is not None
    row = live.row(0, named=True)
    assert row["variant"] == model.variant.name
    assert row["cost"] == pytest.approx(informed["costs"][row["isin"]])
    res = pick.live_results(panel, live)
    if np.isfinite(res["gross"][0]):
        assert res["net"][0] == pytest.approx(res["gross"][0] - row["cost"])
    # an old record without a cost column is still read, at the flat cost
    old = live.drop("cost", "variant").with_columns(pl.col("week") - timedelta(days=7))
    res_old = pick.live_results(panel, old)
    assert res_old.height == 1


def test_band_is_calibrated_and_brackets_the_forecast(informed):
    s = informed["with"]["summary"]
    assert s["band_all_lo_q"] < 0 < s["band_all_hi_q"]
    # the fixture's state has no regime: the pooled band is used
    assert s["regime"] is None and s["band_used"] == "all"
    # split conformal on a stationary synthetic market: close to the nominal 90 %
    assert s["band_coverage"] == pytest.approx(0.90, abs=0.05)
    scores = informed["with"]["scores"]
    assert (scores["band_lo"] < scores["expected"]).all()
    assert (scores["expected"] < scores["band_hi"]).all()
    # a calm stock (smaller σ) gets a narrower band than a wild one
    width = (scores["band_hi"] - scores["band_lo"]).to_numpy()
    sig = np.array([0.06 + 0.01 * (int(i[-1]) % 3) for i in scores["isin"]])
    assert np.corrcoef(width, sig)[0, 1] > 0.99


def test_no_errors_no_band():
    prices, bench, events, cal = market(n_stocks=20, n_sessions=400)
    panel = pf.build_panel(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))
    weeks = pick.build_weeks(panel)
    assert pick.calibrate_bands(weeks[:5], panel, pick.BASE, 10.0) == {}
    m = pick.Model(np.ones(len(pf.FEATURES)), 1.0, 0.0, 1.0, 50)
    assert m.band_for("stress") is None and m.interval(np.zeros((1, 27)), np.ones(1)) is None


@pytest.fixture(scope="module")
def regimes():
    """Calm stretches and stress stretches; in stress, stocks are 2.5 times as noisy against
    WIG. The state's σ does not know it; its regime column does."""
    n, t_n = 40, 1300
    rng = np.random.default_rng(31)
    prices, bench, events, cal = market(n_stocks=n, n_sessions=t_n, seed=31)
    stress = (np.arange(t_n) % 180) >= 120  # 60 stress sessions in every 180
    noise = np.where(stress, 0.03, 0.012)
    mkt = bench["wig_ret"].to_numpy()
    isins = sorted(set(prices["isin"].to_list()))
    rows = []
    for i, isin in enumerate(isins):
        r = mkt + noise * rng.standard_normal(t_n)
        price = 20 * np.cumprod(1 + r)
        for j, d in enumerate(cal):
            rows.append(
                {
                    "isin": isin,
                    "trade_date": d,
                    "ticker": f"S{i:02d}",
                    "close": float(price[j]),
                    "adj_ret": float(r[j]) if j else None,
                    "value_pln": 500_000.0,
                    "ca": 0,
                    "known_at": _known(d),
                }
            )
    prices = pl.DataFrame(rows)
    state = _state(prices, {s: np.zeros(t_n) for s in isins}, cal, dict.fromkeys(isins, 0.01)).join(
        pl.DataFrame({"trade_date": cal, "regime_stress": stress.astype(float)}),
        on="trade_date",
    )
    out = pick.compute(pf.cut_inputs(prices, bench, events, _as_of(cal[-1]), state))
    return out


def test_stress_gets_its_own_wider_band(regimes):
    s = regimes["summary"]
    calm = s["band_calm_hi_q"] - s["band_calm_lo_q"]
    wild = s["band_stress_hi_q"] - s["band_stress_lo_q"]
    assert wild > 1.6 * calm
    bt = regimes["backtest"]
    assert set(bt["band"].drop_nulls()) <= {"calm", "stress", "all"}
    assert (bt.filter(pl.col("regime") == "stress")["band"] == "stress").any()


def test_per_regime_bands_hold_in_both_regimes(regimes):
    bt = regimes["backtest"].filter(pl.col("coverage").is_not_nan())
    by = {r: bt.filter(pl.col("regime") == r)["coverage"].mean() for r in ("calm", "stress")}
    # weeks near a change of regime straddle both, so allow some slack around 90 %
    assert by["calm"] == pytest.approx(0.90, abs=0.07)
    assert by["stress"] == pytest.approx(0.90, abs=0.10)


def test_pooled_band_would_miss_in_stress(regimes):
    """The reason for the per-regime band: one pooled band is too narrow in stress."""
    model = regimes["model"]
    pooled = pick.Model(
        model.weights,
        model.penalty,
        model.intercept,
        model.slope,
        model.train_weeks,
        variant=model.variant,
        errors={"all": model.errors["all"]},
    )
    weeks = [
        w
        for w in pick.build_weeks(regimes["panel"])
        if w.regime == "stress" and np.isfinite(w.y).any()
    ]
    own = np.nanmean([pick.coverage(model, w) for w in weeks])
    one = np.nanmean([pick.coverage(pooled, w) for w in weeks])
    assert own > one + 0.05


# --------------------------------------------------------------------------- self-correcting band


def test_aci_moves_alpha_only_on_known_weeks():
    h = pf.HORIZON
    assert pick.aci_alpha([], 100) == pick.ALPHA
    # a week that missed 30 % of its outcomes widens later bands (smaller α)...
    a = pick.aci_alpha([(10, 0.3)], 10 + h + 1)
    assert a == pytest.approx(pick.ALPHA + pick.ACI_GAMMA * (pick.ALPHA - 0.3))
    # ...but only once its 20-session outcome is known
    assert pick.aci_alpha([(10, 0.3)], 10 + h) == pick.ALPHA
    # never missing narrows them; α stays within its bounds
    many = [(t, 0.0) for t in range(0, 5000, 5)]
    assert pick.aci_alpha(many, 10_000) == pick.ALPHA_BOUNDS[1]
    worst = [(t, 1.0) for t in range(0, 5000, 5)]
    assert pick.aci_alpha(worst, 10_000) == pick.ALPHA_BOUNDS[0]


@pytest.fixture(scope="module")
def drifting():
    """Stocks get steadily noisier against WIG; the state's σ and regime do not show it."""
    n, t_n = 40, 1300
    rng = np.random.default_rng(41)
    prices, bench, events, cal = market(n_stocks=n, n_sessions=t_n, seed=41)
    noise = np.linspace(0.008, 0.03, t_n)
    mkt = bench["wig_ret"].to_numpy()
    isins = sorted(set(prices["isin"].to_list()))
    rows = []
    for i, isin in enumerate(isins):
        r = mkt + noise * rng.standard_normal(t_n)
        price = 20 * np.cumprod(1 + r)
        for j, d in enumerate(cal):
            rows.append(
                {
                    "isin": isin,
                    "trade_date": d,
                    "ticker": f"S{i:02d}",
                    "close": float(price[j]),
                    "adj_ret": float(r[j]) if j else None,
                    "value_pln": 500_000.0,
                    "ca": 0,
                    "known_at": _known(d),
                }
            )
    prices = pl.DataFrame(rows)
    state = _state(prices, {s: np.zeros(t_n) for s in isins}, cal, dict.fromkeys(isins, 0.01))
    inputs = pf.cut_inputs(prices, bench, events, _as_of(cal[-1]), state)
    return inputs, cal


def test_aci_keeps_coverage_up_when_noise_drifts(drifting, monkeypatch):
    inputs, _ = drifting
    panel = pf.build_panel(inputs)
    weeks = pick.build_weeks(panel)

    def cov(gamma: float) -> float:
        monkeypatch.setattr(pick, "ACI_GAMMA", gamma)
        bt = pick.walk_forward(weeks, panel, variant=pick.VARIANTS[1])
        return float(np.nanmean([r["coverage"] for r in bt.rows]))

    fixed, adaptive = cov(0.0), cov(0.05)
    assert fixed < 0.85  # calibrated on calmer years, a fixed band falls behind
    assert adaptive > fixed + 0.02
    assert abs(adaptive - 0.9) < abs(fixed - 0.9)


# --------------------------------------------------------------------------- forecast ledger


def _monday_inputs(n_sessions: int = 620):
    prices, bench, events, cal = market(n_stocks=25, n_sessions=n_sessions, seed=51)
    mondays = [
        d for k, d in enumerate(cal) if k and d.isocalendar()[:2] != cal[k - 1].isocalendar()[:2]
    ]
    day = mondays[-1]
    keep = pl.col("trade_date") <= day
    return pf.cut_inputs(prices.filter(keep), bench.filter(keep), events, _as_of(day)), day


def test_ledger_is_written_once_per_week_with_every_scored_stock(tmp_path):
    inputs, day = _monday_inputs()
    out = pick.compute(inputs)
    assert out["scores"].height >= pick.MIN_UNIVERSE
    first = pick.record_forecasts(tmp_path, out["panel"], out["scores"], out["summary"], day)
    assert first is not None and first.height == out["scores"].height
    assert set(first["week"]) == {day}
    assert (first["band_lo"] < first["expected"]).all() and (
        first["alpha"] == out["summary"]["alpha"]
    ).all()
    again = pick.record_forecasts(tmp_path, out["panel"], out["scores"], out["summary"], day)
    assert again is not None and again.equals(first)  # never rewritten
    # a day that is not the week's first session writes nothing
    tuesday_like = out["panel"].cal[-2]
    assert pick.record_forecasts(
        tmp_path, out["panel"], out["scores"], out["summary"], tuesday_like
    ).equals(first)


def test_ledger_misses_set_later_alpha():
    inputs, _ = _monday_inputs()
    base = pick.compute(inputs)
    panel = base["panel"]
    # a ledger from earlier weeks whose bands had no width: every known outcome missed
    early = [w for w in pick.build_weeks(panel) if w.t + pf.HORIZON < len(panel.cal) - 30][-6:]
    ledger = pl.DataFrame(
        [
            {
                "week": w.day,
                "isin": panel.isins[i],
                "expected": 0.0,
                "band_lo": 0.0,
                "band_hi": 0.0,
                "alpha": pick.ALPHA,
            }
            for w in early
            for i in w.stocks
        ]
    )
    cov = pick.ledger_coverage(panel, ledger)
    assert cov.height == len(early) and (cov["coverage"] == 0.0).all()
    with_ledger = pick.compute(inputs, ledger)
    s = with_ledger["summary"]
    assert s["ledger_weeks"] == len(early) and s["live_coverage"] == 0.0
    assert s["alpha"] < base["summary"]["alpha"]  # a ledger that missed widens the band
    w_new = (with_ledger["scores"]["band_hi"] - with_ledger["scores"]["band_lo"]).mean()
    w_old = (base["scores"]["band_hi"] - base["scores"]["band_lo"]).mean()
    assert w_new > w_old
