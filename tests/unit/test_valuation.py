"""Valuation: yields and their fallbacks, peers, reverse DCF, F-score, Altman's Z, history."""

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from undercurrent import valuation as v

CUR = {
    "revenue": 1000.0,
    "gross_profit": 400.0,
    "operating_profit": 150.0,
    "net_profit": 100.0,
    "net_profit_parent": 100.0,
    "operating_cash_flow": 180.0,
    "capex_ppe": -60.0,  # outflows may be tagged negative or positive
    "capex_intangibles": 20.0,
    "eps_basic": 2.0,
    "shares_weighted": None,
    "assets": 2000.0,
    "liabilities": 800.0,
    "equity_parent": 1200.0,
    "current_assets": 700.0,
    "current_liabilities": 350.0,
    "noncurrent_liabilities": 450.0,
    "retained_earnings": 500.0,
}
PREV = {
    **CUR,
    "revenue": 900.0,
    "gross_profit": 330.0,
    "net_profit": 70.0,
    "net_profit_parent": 70.0,
    "eps_basic": 1.4,
    "assets": 1900.0,
    "current_assets": 600.0,
    "current_liabilities": 350.0,
    "noncurrent_liabilities": 500.0,
}
REPORT = {
    "isin": "PLX",
    "known_at": datetime(2025, 4, 30, 12, tzinfo=UTC),
    "fiscal_year_end": date(2024, 12, 31),
    "currency": "PLN",
    "cur": CUR,
    "prev": PREV,
}


def test_free_cash_flow_and_shares_from_the_report() -> None:
    assert v.free_cash_flow(CUR) == pytest.approx(180 - 60 - 20)
    assert v.free_cash_flow({**CUR, "capex_ppe": None}) is None
    combined = {**CUR, "capex_ppe": None, "capex_intangibles": None, "capex_combined": -90.0}
    assert v.free_cash_flow(combined) == pytest.approx(180 - 90)
    assert v.shares_from_report(CUR) == pytest.approx(50.0)  # 100 / 2.0
    assert v.shares_from_report({**CUR, "shares_weighted": 48.0}) == 48.0
    assert v.shares_from_report({**CUR, "eps_basic": 0.0}) is None


def test_yields_prefer_gpw_and_fall_back_to_the_report() -> None:
    gpw = {"shares": 40, "pe": 10.0, "market_value_pln": 800.0, "book_value_pln": 1000.0}
    y = v.yields(20.0, {**gpw, "dividend_yield": 0.03}, REPORT)
    assert y["market_cap"] == pytest.approx(800.0) and y["shares_source"] == "gpw"
    assert y["ep"] == pytest.approx(80.0 / 800.0)  # GPW's earnings: 800 / 10
    assert y["bp"] == pytest.approx(1000.0 / 800.0)
    assert y["fcf_yield"] == pytest.approx(100.0 / 800.0)
    assert y["ebit_yield"] == pytest.approx(150.0 / 800.0)
    assert y["dividend_yield"] == pytest.approx(0.03)
    r = v.yields(20.0, None, REPORT)  # no GPW table: shares 50 from profit / EPS
    assert r["market_cap"] == pytest.approx(1000.0) and r["shares_source"] == "report"
    assert r["ep"] == pytest.approx(0.1) and r["bp"] == pytest.approx(1.2)
    loss = v.yields(
        20.0, {**gpw, "pe": None}, {**REPORT, "cur": {**CUR, "net_profit_parent": -50.0}}
    )
    assert loss["ep"] == pytest.approx(-50.0 / 800.0)  # a loss is a low yield, not a gap


def test_reverse_dcf_recovers_the_growth_it_was_built_with() -> None:
    for r in v.DISCOUNT_RATES:
        value = v._present_value(100.0, 0.07, r)
        assert v.implied_growth(100.0, value, r) == pytest.approx(0.07, abs=1e-6)
    assert v.implied_growth(100.0, 1e9, 0.10) == v.GROWTH_RANGE[1]  # more than the range
    assert v.implied_growth(100.0, 1.0, 0.10) == v.GROWTH_RANGE[0]
    assert v.implied_growth(-5.0, 1000.0, 0.10) is None


def test_piotroski_by_hand() -> None:
    f = v.piotroski(CUR, PREV)
    t = f["tests"]
    assert t["roa_positive"] and t["cfo_positive"] and t["cash_above_profit"]
    assert t["roa_up"]  # 100/2000 = 5.0 % > 70/1900 = 3.7 %
    assert t["leverage_down"]  # 450/2000 < 500/1900
    assert t["liquidity_up"]  # 2.0 > 1.71
    assert t["no_new_shares"]  # 50 shares both years
    assert t["margin_up"]  # 40 % > 36.7 %
    assert t["turnover_up"]  # 0.5 > 0.47
    assert f["score"] == 9 and f["seen"] == 9
    g = v.piotroski({**CUR, "gross_profit": None}, PREV)
    assert g["tests"]["margin_up"] is None and g["seen"] == 8


def test_altman_by_hand() -> None:
    z = v.altman(CUR, 1000.0)
    expect = 1.2 * 350 / 2000 + 1.4 * 500 / 2000 + 3.3 * 150 / 2000 + 0.6 * 1000 / 800 + 1000 / 2000
    assert z is not None and z["z"] == pytest.approx(expect) and z["zone"] == "grey"
    assert v.altman({**CUR, "retained_earnings": None}, 1000.0) is None
    assert v.is_financial("Finanse - banki") and not v.is_financial("Handel i usługi - Gry")


def test_percentiles_use_the_sector_or_the_market() -> None:
    rows = [
        {"isin": f"S{i}", "sector_code": 1, "ep": i / 10, "bp": 1.0, "fcf_yield": None}
        for i in range(6)
    ]
    rows += [{"isin": "T0", "sector_code": 2, "ep": 0.9, "bp": 0.5, "fcf_yield": 0.1}]
    v.percentiles(rows, v.VALUE_KEYS)
    top = rows[5]
    assert top["ep_pct"] == pytest.approx(11 / 12) and top["peer_group"] == "sector"
    lone = rows[-1]
    assert lone["peer_group"] == "market"  # sector 2 has one stock: ranked among all
    assert lone["ep_pct"] == pytest.approx(13 / 14)
    assert top["value_score"] == pytest.approx((top["ep_pct"] + top["bp_pct"]) / 2)
    assert top["value_score_pct"] == max(r["value_score_pct"] for r in rows[:6])


def test_history_events_when_a_state_begins() -> None:
    cal = [date(2025, 5, 5) + timedelta(days=i) for i in range(60)]
    cal = [d for d in cal if d.weekday() < 5]
    # one cheap stock among six peers, priced so that it becomes cheap in week three
    prices = []
    for d in cal:
        for i in range(6):
            c = 20.0 if i else (40.0 if d < date(2025, 5, 19) else 10.0)
            prices.append(
                {
                    "isin": f"S{i}",
                    "trade_date": d,
                    "close": c,
                    "known_at": datetime.combine(d, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=16),
                    "market": "GPW",
                }
            )
    statements = [{**REPORT, "isin": f"S{i}"} for i in range(6)]
    sectors = {f"S{i}": (650, "Handel i usługi - Gry") for i in range(6)}
    ev = v.history_events(pl.DataFrame(prices), statements, sectors, cal)
    cheap = ev.filter(pl.col("subtype") == "cheap_vs_sector")
    assert cheap["isin"].to_list() == ["S0"]
    assert cheap["event_date"][0] == date(2025, 5, 19)  # the first Monday it is cheap
    # quality is a state from the first week on: no event (it did not *begin* in sample)
    assert ev.filter(pl.col("subtype") == "quality").height == 0


def test_peers_fall_back_to_the_sector_group() -> None:
    rows = [
        {
            "isin": f"B{i}",
            "sector_code": 100 + i % 2,
            "sector": f"Finanse - typ {i % 2}",
            "ep": i / 10,
            "bp": 1.0,
            "fcf_yield": 0.1,
        }
        for i in range(8)
    ]  # two sectors of four: too few alone, eight as the group "Finanse"
    rows.append(
        {
            "isin": "X",
            "sector_code": 900,
            "sector": "Inne - x",
            "ep": 0.5,
            "bp": 1.0,
            "fcf_yield": 0.1,
        }
    )
    v.percentiles(rows, v.VALUE_KEYS)
    assert {r["peer_group"] for r in rows[:8]} == {"group"}
    assert rows[-1]["peer_group"] == "market"
    assert v.sector_group("Finanse - banki komercyjne") == "Finanse"
