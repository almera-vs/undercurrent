"""The basket (L4) and the event page reading the shared layers."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from tests.unit.test_pick import _as_of, market
from tests.unit.test_pick_state import _state
from undercurrent import basket, pages, pick, publish
from undercurrent import pick_features as pf


def test_choose_keeps_held_names_inside_the_buffer_and_drops_losers():
    stocks = np.arange(30)
    expected = np.linspace(0.06, -0.03, 30)  # stock 0 best, the last ones negative
    cost = np.full(30, 0.01)
    fresh = basket.choose({}, stocks, expected, cost)
    assert fresh == list(range(basket.K))
    # stock 15 is outside the top 10 but inside the top 20: a holder keeps it
    kept = basket.choose({15: 0.1}, stocks, expected, cost)
    assert 15 in kept and len(kept) == basket.K
    # stock 25 is outside the buffer: it goes
    assert 25 not in basket.choose({25: 0.1}, stocks, expected, cost)
    # nothing beats its cost: nothing is held
    assert basket.choose({}, stocks, np.zeros(30), cost) == []


def test_hrp_gives_an_independent_name_more_than_two_twins():
    cov = np.array([[1.0, 0.95, 0.0], [0.95, 1.0, 0.0], [0.0, 0.0, 1.0]]) * 1e-4
    w = basket.hrp(cov)
    assert w.sum() == pytest.approx(1.0) and (w > 0).all()
    assert w[2] > w[0] and w[2] > w[1]
    assert w[0] == pytest.approx(w[1])
    assert basket.hrp(np.array([[2e-4]]))[0] == 1.0


def test_cap_and_partial_investment():
    w = basket._cap(np.array([0.6, 0.2, 0.2]), 0.4)
    assert w.max() <= 0.4 + 1e-12 and w.sum() == pytest.approx(1.0)
    prices, bench, events, cal = market(n_stocks=20, n_sessions=400)
    panel = pf.build_panel(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))
    resid = basket.daily_residuals(panel)
    out = basket.weights(panel, resid, len(cal) - 1, [0, 1, 2], {0: 0.05, 1: 0.05, 2: 0.05})
    assert sum(out.values()) == pytest.approx(3 / basket.K)  # the rest stays in WIG
    assert max(out.values()) <= basket.MAX_WEIGHT


def test_trades_pay_only_on_increases():
    cost = {1: 0.02, 2: 0.01}
    assert basket.trade_cost({}, {1: 0.5}, cost) == pytest.approx(0.01)
    assert basket.trade_cost({1: 0.5}, {1: 0.3, 2: 0.2}, cost) == pytest.approx(0.002)
    assert basket.trade_cost({1: 0.5}, {}, cost) == 0.0


@pytest.fixture(scope="module")
def with_basket():
    prices, bench, events, cal = market(n_stocks=30, n_sessions=900, drift=0.0015, seed=61)
    isins = sorted(set(prices["isin"].to_list()))
    state = _state(prices, {s: np.zeros(len(cal)) for s in isins}, cal, dict.fromkeys(isins, 0.005))
    return pick.compute(pf.cut_inputs(prices, bench, events, _as_of(cal[-1]), state)), cal


def test_basket_is_walked_forward_net_of_its_trades(with_basket):
    out, _ = with_basket
    bt = out["basket_backtest"]
    assert bt.height == out["backtest"].height
    assert (bt["names"] <= basket.K).all() and (bt["invested"] <= 1 + 1e-9).all()
    ok = bt.filter(pl.col("gross").is_not_nan())
    assert ((ok["net"] - (ok["gross"] - ok["cost"])).abs() < 1e-12).all()
    first = out["basket_weights"].filter(pl.col("day") == bt["day"][0])
    assert bt["cost"][0] == pytest.approx(first["weight"].sum() * 0.005)
    s = out["summary"]
    assert s["basket_weeks"] == ok.height and s["basket_verdict"] in {
        "beats_wig",
        "unconfirmed",
        "no_edge",
        "too_short",
    }
    # momentum is planted: holding several of the best names beats the whole universe
    assert s["basket_total_net"] > s["basket_total"]


def test_basket_is_recorded_once_per_week(tmp_path):
    prices, bench, events, cal = market(n_stocks=25, n_sessions=620, drift=0.0015, seed=62)
    mondays = [
        d for k, d in enumerate(cal) if k and d.isocalendar()[1] != cal[k - 1].isocalendar()[1]
    ]
    day = mondays[-2]
    keep = pl.col("trade_date") <= day
    out = pick.compute(pf.cut_inputs(prices.filter(keep), bench.filter(keep), events, _as_of(day)))
    now = out["basket_now"]
    assert now.height and now["weight"].sum() <= 1 + 1e-9
    first = pick.record_basket(tmp_path, out["panel"], now, out["basket_cost"], day, _as_of(day))
    assert first is not None and set(first["week"]) == {day}
    again = pick.record_basket(tmp_path, out["panel"], now, out["basket_cost"], day, _as_of(day))
    assert again is not None and again.equals(first)
    # the next week starts from what was held
    held = pick._held_from(out["panel"], first, day + timedelta(days=7))
    assert sum(held.values()) == pytest.approx(now["weight"].sum())
    full = pf.build_panel(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))
    res = pick.basket_live_results(full, first)
    assert res.height == 1
    assert res["net"][0] == pytest.approx(res["gross"][0] - res["cost"][0])


# --------------------------------------------------------------------------- event page


def _history(n: int = 80):
    days = [date(2018, 1, 1) + timedelta(days=i) for i in range(n)]
    return pages.PriceHistory(
        days, [10.0] * n, [10.0] * n, [0.0] + [0.01] * (n - 1), [500_000.0] * n
    ), days


def test_event_page_reads_the_market_state_and_the_forecast():
    h, days = _history()
    d0 = days[70]
    st = pl.DataFrame(
        {
            "trade_date": days,
            "surprise": [0.0] * 70 + [-2.5] + [0.1] * 9,
            "sigma_20": [0.08] * 80,
            "cost": [0.012] * 80,
            "liquidity_tier": ["liquid"] * 80,
            "median_close": [10.0] * 80,
            "median_turnover": [400_000.0] * 80,
        }
    )
    fc = {"expected": 0.01, "band_lo": -0.1, "band_hi": 0.12, "news_effect": 0.002}
    e = {"day0": d0, "pub_date": d0, "entry_date": None, "horizon": 20}
    wig = dict.fromkeys(days, 0.0)
    c = publish._company(e, h, wig, days, days[-1], st, fc)
    assert c.tier == "liquid" and c.buyable is True
    assert c.surprise == -2.5 and c.sigma_20 == 0.08 and c.cost == 0.012
    assert c.median_turnover == 400_000.0  # the state's, not the page's own
    assert c.forecast is fc
    # without the state the page falls back to its own liquidity rule and shows no state
    bare = publish._company(e, h, wig, days, days[-1])
    assert bare.tier is None and bare.surprise is None and bare.buyable is True


def test_a_forecast_from_another_day_is_not_shown(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    pl.DataFrame({"isin": ["X"], "expected": [0.01]}).write_parquet(gold / "pick_scores.parquet")
    pl.DataFrame({"as_of": [_as_of(date(2018, 12, 10))]}).write_parquet(
        gold / "pick_summary.parquet"
    )
    assert publish._forecast_rows(tmp_path, date(2018, 12, 14)) == {}
    from undercurrent.study import as_of_for

    pl.DataFrame({"as_of": [as_of_for(date(2018, 12, 14))]}).write_parquet(
        gold / "pick_summary.parquet"
    )
    rows = publish._forecast_rows(tmp_path, date(2018, 12, 14))
    assert rows["X"]["expected"] == 0.01 and rows["X"]["universe"] == 1


# --------------------------------------------------------------------------- the headline


def test_headline_is_the_baskets_largest_position(with_basket):
    out, _ = with_basket
    s = out["summary"]
    now = out["basket_now"]
    if now.height:
        assert s["headline_source"] == "basket"
        assert s["headline_isin"] == now["isin"][0]
        assert s["headline_weight"] == now["weight"].max()
    assert s["verdict"] == s["basket_verdict"]
    assert s["single_verdict"] in {"beats_wig", "unconfirmed", "no_edge", "too_short"}


def test_headline_falls_back_to_the_top_score_when_nothing_beats_its_cost():
    empty = pl.DataFrame(schema={"isin": pl.String, "ticker": pl.String, "weight": pl.Float64})
    scores = pl.DataFrame({"isin": ["A", "B"], "ticker": ["AA", "BB"]})
    h = pick._headline(empty, scores)
    assert (h["headline_isin"], h["headline_source"], h["headline_weight"]) == (
        "A",
        "top_score",
        0.0,
    )
    assert pick._headline(empty, pl.DataFrame())["headline_isin"] is None


def test_headline_record_counts_consecutive_weeks_in_the_live_basket():
    w = [date(2026, 11, 16) + timedelta(days=7 * k) for k in range(4)]
    live = pl.DataFrame(
        {
            "week": [w[0], w[1], w[1], w[2], w[3], w[3]],
            "isin": ["A", "B", "A", "A", "A", "C"],
            "weight": [0.2, 0.1, 0.2, 0.2, 0.2, 0.1],
        }
    )
    assert pick.headline_record(live, "A") == {"headline_live": True, "headline_since": w[0]}
    assert pick.headline_record(live, "C") == {"headline_live": True, "headline_since": w[3]}
    assert pick.headline_record(live, "B") == {"headline_live": False, "headline_since": None}
    assert pick.headline_record(None, "A")["headline_live"] is False
