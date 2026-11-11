"""Stock of the week: features, model, walk-forward, verdict and live record."""

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from tests.study_fixtures import sessions
from undercurrent import lake, pick
from undercurrent import pick_features as pf

WARSAW = ZoneInfo("Europe/Warsaw")


def _known(d: date) -> datetime:
    return datetime.combine(d, time(17, 35), tzinfo=WARSAW).astimezone(UTC)


def market(
    n_stocks: int = 60, n_sessions: int = 1000, drift: float = 0.0, seed: int = 11
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[date]]:
    """Liquid synthetic stocks. With ``drift``, each stock has a persistent daily excess
    return, so past winners keep winning: momentum is real and a model should find it."""
    rng = np.random.default_rng(seed)
    cal = sessions(n_sessions, start=date(2015, 1, 5))
    mkt = rng.normal(0.0003, 0.01, n_sessions)
    alpha = rng.normal(0, drift, n_stocks)
    rows = []
    for i in range(n_stocks):
        r = mkt + alpha[i] + rng.normal(0, 0.015, n_sessions)
        price = 20 * np.cumprod(1 + r)
        for j, d in enumerate(cal):
            rows.append(
                {
                    "isin": f"PLSTK{i:07d}",
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
    bench = pl.DataFrame(
        {"trade_date": cal, "wig_ret": mkt.tolist(), "known_at": [_known(d) for d in cal]}
    )
    events = pl.DataFrame(
        schema={
            "isin": pl.String,
            "event_date": pl.Date,
            "signal_type": pl.String,
            "subtype": pl.String,
            "insider_side": pl.String,
            "insider_value": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        }
    )
    return prices, bench, events, cal


def _as_of(d: date) -> datetime:
    return datetime.combine(d, time(23, 59), tzinfo=WARSAW).astimezone(UTC)


@pytest.fixture(scope="module")
def planted() -> dict:
    prices, bench, events, cal = market(drift=0.0015)
    return pick.compute(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))


def test_planted_momentum_is_found(planted):
    weights = dict(zip(pf.FEATURES, planted["model"].weights, strict=True))
    assert weights["mom_12_1"] > 0 and weights["mom_6_1"] > 0
    s = planted["summary"]
    assert s["oos_weeks"] >= pick.MIN_OOS_WEEKS
    assert s["ic_mean"] > 0.1
    assert s["top10_mean"] > s["basket_mean"]


def test_no_effect_is_not_called_an_edge():
    prices, bench, events, cal = market(drift=0.0, seed=5)
    s = pick.compute(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))["summary"]
    assert s["verdict"] != "beats_wig"
    assert abs(s["ic_mean"]) < 0.06


def test_later_rows_change_nothing():
    prices, bench, events, cal = market(n_stocks=30, n_sessions=700, drift=0.001)
    cut = _as_of(cal[600])
    full = pick.compute(pf.cut_inputs(prices, bench, events, cut))
    short = pick.compute(
        pf.cut_inputs(
            prices.filter(pl.col("trade_date") <= cal[600]),
            bench.filter(pl.col("trade_date") <= cal[600]),
            events,
            cut,
        )
    )
    assert full["scores"].equals(short["scores"])
    assert full["backtest"].equals(short["backtest"])


def test_training_targets_end_before_the_test_year():
    prices, bench, events, cal = market(n_stocks=30, n_sessions=800)
    panel = pf.build_panel(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))
    weeks = pick.build_weeks(panel)
    for y in sorted({w.day.year for w in weeks})[1:]:
        start = next(w.t for w in weeks if w.day.year == y)
        train = pick._known_before([w for w in weeks if w.day.year < y], panel, start)
        assert train and all(w.t + pf.HORIZON < start for w in train)
        assert len(train) < len([w for w in weeks if w.day.year < y])  # some were purged


def test_hysteresis_keeps_a_pick_unless_the_gain_beats_the_cost():
    stocks = np.array([3, 7, 9])
    held = pick.Holding(7, 0.03)
    keep, switched = pick.pick_with_hysteresis(held, stocks, np.array([0.04, 0.03, 0.0]))
    assert (keep.stock, switched) == (7, False)  # +1 pp is less than a 2 % round trip
    move, switched = pick.pick_with_hysteresis(held, stocks, np.array([0.06, 0.03, 0.0]))
    assert (move.stock, switched) == (3, True)
    gone, switched = pick.pick_with_hysteresis(pick.Holding(5, 0.1), stocks, np.zeros(3))
    assert switched and gone.stock in stocks  # the old pick left the universe
    first, switched = pick.pick_with_hysteresis(None, stocks, np.array([0.0, 0.01, 0.0]))
    assert (first.stock, switched) == (7, True)


def test_outputs_are_reproducible(tmp_path, planted):
    prices, bench, events, cal = market(drift=0.0015)
    again = pick.compute(pf.cut_inputs(prices, bench, events, _as_of(cal[-1])))
    shas = []
    for i, out in enumerate((planted, again)):
        path = tmp_path / f"run{i}.parquet"
        lake.write_frame(out["scores"], path)
        shas.append(hashlib.sha256(path.read_bytes()).hexdigest())
    assert shas[0] == shas[1]
    assert planted["backtest"].equals(again["backtest"])


def test_live_record_is_written_once_per_week(tmp_path, planted):
    panel, model = planted["panel"], planted["model"]
    monday = next(d for d in reversed(panel.cal) if pick.is_rebalance_day(panel, d))
    tuesday = panel.cal[panel.cal.index(monday) + 1] if monday != panel.cal[-1] else None
    as_of = _as_of(monday)
    first = pick.record_live(tmp_path, panel, model, monday, as_of)
    assert first is not None and first.height == 1 and first["switched"][0]
    again = pick.record_live(tmp_path, panel, model, monday, as_of + timedelta(hours=1))
    assert again is not None and again.equals(first)  # a rerun never rewrites the week
    if tuesday is not None:
        mid = pick.record_live(tmp_path, panel, model, tuesday, _as_of(tuesday))
        assert mid is not None and mid.height == 1
    res = pick.live_results(panel, first)
    assert res.height == 1 and res["net"][0] == pytest.approx(res["gross"][0] - pick.COST)
