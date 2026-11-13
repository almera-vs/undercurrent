"""ESEF snapshots, earnings drift, abnormal turnover and the fundamental features."""

from datetime import UTC, date, datetime, time
from typing import Any

import numpy as np
import polars as pl
import pytest

from tests.unit.test_pick import WARSAW, _as_of, _known, market
from undercurrent import esef, study
from undercurrent import pick_features as pf

ISIN = "PLSTK0000003"


def _fact(
    filing: str,
    fy: date,
    concept: str,
    value: float,
    end: date,
    start: date | None = None,
    unit: str = "PLN",
    known: datetime = datetime(2017, 5, 10, 8, 0, tzinfo=UTC),
) -> dict[str, Any]:
    return {
        "fact_key": f"{filing}:{concept}:{start}:{end}:{unit}",
        "filing_id": filing,
        "lei": "LEI0",
        "fiscal_period_end": fy,
        "concept": concept,
        "period_start": start,
        "period_end": end,
        "value": value,
        "unit": unit,
        "decimals": 0,
        "known_at": known,
        "isin": ISIN,
    }


FY16, FY15, FY14 = date(2016, 12, 31), date(2015, 12, 31), date(2014, 12, 31)
EPS = "PLN/xbrli:shares"
K1 = datetime(2016, 5, 10, 8, 0, tzinfo=UTC)  # 10:00 Warsaw: the same day's session
K2 = datetime(2017, 5, 10, 16, 30, tzinfo=UTC)  # 18:30 Warsaw: the next session


def _facts() -> pl.DataFrame:
    rows = [
        # the FY2015 report, with FY2014 comparatives
        _fact("1", FY15, "assets", 1000.0, FY15, known=K1),
        _fact("1", FY15, "assets", 800.0, FY14, known=K1),
        _fact("1", FY15, "operating_profit", 100.0, FY15, date(2015, 1, 1), known=K1),
        _fact("1", FY15, "net_profit", 80.0, FY15, date(2015, 1, 1), known=K1),
        _fact("1", FY15, "operating_cash_flow", 50.0, FY15, date(2015, 1, 1), known=K1),
        _fact("1", FY15, "eps_basic", 2.0, FY15, date(2015, 1, 1), unit=EPS, known=K1),
        # a quarter inside the year is not the year
        _fact("1", FY15, "operating_profit", 999.0, FY15, date(2015, 10, 1), known=K1),
        # the FY2016 report, restating FY2015 assets
        _fact("2", FY16, "assets", 1500.0, FY16, known=K2),
        _fact("2", FY16, "assets", 1200.0, FY15, known=K2),
        _fact("2", FY16, "operating_profit", 300.0, FY16, date(2016, 1, 1), known=K2),
        _fact(
            "2", FY16, "eps_basic", 1.0, FY16, date(2016, 1, 1), unit="EUR/xbrli:shares", known=K2
        ),
    ]
    return pl.DataFrame(rows, schema={**esef.FACT_SCHEMA, "isin": pl.String})


def test_snapshots_use_only_what_each_filing_moment_knew() -> None:
    snaps = esef.snapshots(_facts())
    assert snaps.height == 2
    first, second = snaps.to_dicts()
    assert first["known_at"] == K1 and first["fiscal_year_end"] == FY15
    assert first["profitability"] == pytest.approx(0.1)
    assert first["asset_growth"] == pytest.approx(0.25)  # 1000 / 800 - 1
    assert first["accruals"] == pytest.approx(0.03)  # (80 - 50) / 1000
    assert first["eps_pln"] == pytest.approx(2.0)
    # the next year: growth against the restated prior year-end; EPS in EUR is not used
    assert second["fiscal_year_end"] == FY16
    assert second["asset_growth"] == pytest.approx(0.25)  # 1500 / 1200 - 1
    assert second["profitability"] == pytest.approx(0.2)
    assert second["eps_pln"] is None and second["accruals"] is None


def test_no_facts_no_snapshots() -> None:
    empty = pl.DataFrame(schema={**esef.FACT_SCHEMA, "isin": pl.String})
    assert esef.snapshots(empty).height == 0
    assert set(esef.SNAPSHOT_SCHEMA) <= set(esef.snapshots(empty).columns)


@pytest.fixture(scope="module")
def world():
    prices, bench, events, cal = market(n_stocks=20, n_sessions=700, seed=5)
    j0 = 300
    # the surprise column of a market state: 0 every day, 3 on the report's two sessions
    state = pl.DataFrame(
        {
            "isin": [s for s in sorted(set(prices["isin"])) for _ in cal],
            "trade_date": cal * 20,
            "surprise": [
                3.0 if s == ISIN and j in (j0, j0 + 1) else 0.0
                for s in sorted(set(prices["isin"]))
                for j in range(len(cal))
            ],
            "known_at": [_known(d) for d in cal] * 20,
        }
    )
    periodic = pl.DataFrame(
        {
            "isin": [ISIN],
            "event_date": [cal[j0]],
            "known_at": [datetime.combine(cal[j0], time(8, 0), tzinfo=WARSAW).astimezone(UTC)],
        }
    )
    # turnover five times its usual level over the last five sessions
    prices = prices.with_columns(
        pl.when((pl.col("isin") == ISIN) & (pl.col("trade_date") >= cal[-5]))
        .then(pl.col("value_pln") * 5)
        .otherwise(pl.col("value_pln"))
        .alias("value_pln")
    )
    snaps = esef.snapshots(_facts())
    return prices, bench, events, cal, state, periodic, snaps, j0


def _panel(world, day: date | None = None) -> tuple[pf.Panel, list[date]]:
    prices, bench, events, cal, state, periodic, snaps, _ = world
    as_of = _as_of(day or cal[-1])
    return pf.build_panel(pf.cut_inputs(prices, bench, events, as_of, state, periodic, snaps)), cal


def _feature(panel: pf.Panel, t: int, name: str, isin: str = ISIN) -> float:
    return float(pf.raw_features(panel, t)[panel.isins.index(isin), pf.ALL_FEATURES.index(name)])


def test_earnings_drift_is_the_reaction_while_the_report_is_recent(world) -> None:
    panel, _ = _panel(world)
    j0 = world[-1]
    assert _feature(panel, j0, "earn_drift") == pytest.approx(3.0)  # only its own session yet
    assert _feature(panel, j0 + 5, "earn_drift") == pytest.approx(6 / np.sqrt(2))
    assert _feature(panel, j0 + pf.EARN_WINDOW, "earn_drift") == pytest.approx(6 / np.sqrt(2))
    assert np.isnan(_feature(panel, j0 + pf.EARN_WINDOW + 1, "earn_drift"))
    assert np.isnan(_feature(panel, j0 - 1, "earn_drift"))
    assert np.isnan(_feature(panel, j0 + 5, "earn_drift", isin="PLSTK0000004"))


def test_abnormal_turnover_compares_the_last_week_with_the_months_before(world) -> None:
    panel, cal = _panel(world)
    t = len(cal) - 1
    assert _feature(panel, t, "abn_turnover") == pytest.approx(np.log1p(2.5e6) - np.log1p(5e5))
    assert _feature(panel, t, "abn_turnover", isin="PLSTK0000004") == pytest.approx(0.0)


def test_fundamentals_start_at_the_first_close_after_the_filing_and_expire(world) -> None:
    panel, cal = _panel(world)
    i = panel.isins.index(ISIN)
    on = cal.index(date(2016, 5, 10))  # filed at 10:00, so that day's close knows it
    assert np.isnan(_feature(panel, on - 1, "profitability"))
    assert _feature(panel, on, "profitability") == pytest.approx(0.1)
    assert _feature(panel, on, "earnings_yield") == pytest.approx(2.0 / panel.close[i, on])
    nxt = cal.index(date(2017, 5, 11))  # filed at 18:30, after the close
    assert _feature(panel, nxt - 1, "profitability") == pytest.approx(0.1)
    assert _feature(panel, nxt, "profitability") == pytest.approx(0.2)
    assert np.isnan(_feature(panel, nxt, "earnings_yield"))  # EUR EPS
    # without a newer report, FY2015 goes stale 550 days after its year end
    prices, bench, events, _, state, periodic, snaps, _ = world
    only_first = snaps.filter(pl.col("known_at") == K1)
    alone = pf.build_panel(
        pf.cut_inputs(prices, bench, events, _as_of(cal[-1]), state, periodic, only_first)
    )
    stale = next(j for j, d in enumerate(cal) if (d - FY15).days > pf.FUND_STALE_DAYS)
    assert _feature(alone, stale - 1, "profitability") == pytest.approx(0.1)
    assert np.isnan(_feature(alone, stale, "profitability"))


def test_new_features_do_not_change_when_the_future_is_cut_off(world) -> None:
    full, cal = _panel(world)
    j0 = world[-1]
    for t in (j0, j0 + 1, j0 + 30, cal.index(date(2016, 5, 10))):
        cut, _ = _panel(world, cal[t])
        cols = [pf.ALL_FEATURES.index(f) for f in (*pf.EVENT_FEATURES, *pf.FUNDAMENTAL_FEATURES)]
        a = pf.raw_features(full, t)[:, cols]
        b = pf.raw_features(cut, t)[:, cols]
        np.testing.assert_array_equal(a, b)


def test_a_report_known_after_the_cut_is_look_ahead(world) -> None:
    prices, bench, events, cal, state, periodic, snaps, j0 = world
    inputs = pf.cut_inputs(prices, bench, events, _as_of(cal[j0 - 1]), state, periodic, snaps)
    assert inputs.periodic is not None and inputs.periodic.height == 0
    leaked = pf.PickInputs(
        inputs.as_of, inputs.prices, inputs.benchmark, inputs.events, None, periodic, None
    )
    with pytest.raises(study.LookAheadError):
        pf.build_panel(leaked)
