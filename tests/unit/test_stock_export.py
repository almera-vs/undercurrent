"""Data of the analysis pages: point in time, adjusted prices, screener figures, markers."""

import json
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np
import polars as pl
import pytest

from undercurrent import stock_export, ta
from undercurrent.transform import duckdb_path

A, B = "PLAAAAA00011", "PLBBBBB00012"


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture()
def work(tmp_path):
    """300 sessions, then a hole of a month, then 60 more; stock A splits 2:1 on session 100."""
    cal = _weekdays(date(2024, 1, 2), 300) + _weekdays(date(2025, 6, 2), 60)
    hole = [i > 0 and (cal[i] - cal[i - 1]).days > 7 for i in range(len(cal))]
    rng = np.random.default_rng(3)
    rows = []
    for isin, ticker, market in ((A, "AAA", "GPW"), (B, "BBB", "NC")):
        price = 20.0
        for i, d in enumerate(cal):
            price *= float(np.exp(rng.normal(0, 0.02)))
            split = isin == A and i < 100  # before the split the exchange price is double
            close = round(price * (2 if split else 1), 2)
            rows.append(
                {
                    "isin": isin,
                    "trade_date": d,
                    "market": market,
                    "ticker": ticker,
                    "short_name": ticker,
                    "close": close,
                    "adj_close": close / 2 if split else close,
                    "open": close * 0.99,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "volume": 1000.0,
                    "value_pln": 50_000.0,
                }
            )
    px = pl.DataFrame(rows).sort(["isin", "trade_date"])
    px = px.with_columns(
        (pl.col("adj_close") / pl.col("adj_close").shift(1).over("isin") - 1).alias("adj_ret")
    )
    wig = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(cal))))
    sessions = pl.DataFrame({"trade_date": cal, "after_hole": hole})
    idx = pl.DataFrame({"name": ["WIG"] * len(cal), "trade_date": cal, "close": wig.tolist()})
    ts = datetime(2024, 3, 1, 9, tzinfo=UTC)
    events = pl.DataFrame(
        {
            "report_id": ["gpw:1", "gpw:2"],
            "isin": [A, A],
            "event_date": [cal[50], cal[-1] + timedelta(days=30)],  # the second is in the future
            "signal_type": ["insider_tx", "insider_tx"],
            "subtype": [None, None],
            "insider_side": ["buy", "buy"],
            "insider_value": [100_000.0, 5.0],
            "title": ["Transakcja", "Później"],
        }
    )
    reports = pl.DataFrame({"isin": [A], "issuer_name": ["Alfa S.A."], "known_at": [ts]})
    w = tmp_path / "work"
    w.mkdir()
    con = duckdb.connect(str(duckdb_path(w)))
    con.register("px", px.to_arrow())
    con.execute(
        "create table adjusted_prices as select isin, trade_date, market, ticker, short_name, "
        "close, adj_close, adj_ret, volume, value_pln from px"
    )
    con.execute(
        "create table prices_daily as select isin, trade_date, open, high, low, close from px"
    )
    for name, df in (
        ("sessions", sessions),
        ("index_returns", idx),
        ("events", events),
        ("reports", reports),
    ):
        con.register("t", df.to_arrow())
        con.execute(f"create table {name} as select * from t")
        con.unregister("t")
    con.close()
    (w / "gold").mkdir()
    pl.DataFrame(
        {
            "isin": [A, B],
            "trade_date": [cal[-1], cal[-1]],
            "sigma_20": [0.08, 0.2],
            "beta_wig": [1.1, 0.4],
            "surprise": [3.5, -0.2],
            "regime_stress": [0.2, 0.2],
            "liquidity_tier": ["tradable", "thin"],
            "median_turnover": [50_000.0, 50_000.0],
            "cost": [0.02, 0.06],
            "signal_effect": [0.0, 0.0],
        }
    ).write_parquet(w / "gold" / "market_state.parquet")
    return w, cal, px


def test_files_stop_at_the_run_session(work) -> None:
    w, cal, _ = work
    day = cal[-11]  # ten sessions before the end of the data
    tool = stock_export.build(w, day)
    m = json.loads((tool.staging / "market.json").read_text())
    assert m["cal"][-1] == day.isoformat()
    s = json.loads((tool.staging / "stocks" / f"{A}.json").read_text())
    rows = sum(r[1] for r in s["pos"])
    assert rows == len(s["c"]) == len(cal) - 10
    assert all(e[0] < rows for e in s["ev"])
    assert len(s["ev"]) == 1  # the future event is not there
    _row, kind, title, page = s["ev"][0]
    assert s["kinds"][kind] == "insider_tx:buy" and title == "Transakcja" and page is None


def test_adjusted_prices_and_runs(work) -> None:
    w, cal, px = work
    tool = stock_export.build(w, cal[-1])
    s = json.loads((tool.staging / "stocks" / f"{A}.json").read_text())
    assert s["pos"] == [[0, len(cal)]]
    assert s["adj"] == [[0, 0.5], [100, 1.0]]  # the split's factor until it happened
    f = [0.5] * 100 + [1.0] * (len(cal) - 100)
    adj = np.array(s["c"]) * np.array(f)
    ref = px.filter(pl.col("isin") == A)["adj_close"].to_numpy()
    np.testing.assert_allclose(adj, ref, rtol=1e-9)
    m = json.loads((tool.staging / "market.json").read_text())
    assert m["seg"] == [[0, 0.0], [300, 1.0]]  # the hole starts a new segment


def test_screener_figures(work) -> None:
    w, cal, px = work
    tool = stock_export.build(w, cal[-1], forecast={A: {"expected": 0.03, "rank": 2}})
    rows = {r["isin"]: r for r in tool.screener}
    a = rows[A]
    adj = px.filter(pl.col("isin") == A)["adj_close"].to_numpy()
    seg = np.array([0] * 300 + [1] * 60)
    assert a["rsi"] == pytest.approx(ta.rsi(adj, 14, seg)[-1], rel=1e-3)
    assert a["d50"] == pytest.approx(adj[-1] / ta.sma(adj, 50, seg)[-1] - 1, rel=1e-3)
    assert a["d200"] is None  # 60 sessions since the hole: no 200-session average
    assert a["x250"] is None  # nor a year of returns
    assert a["x20"] is not None
    assert a["z"] == pytest.approx(3.5) and a["tier"] == "tradable"
    assert a["fc"] == pytest.approx(0.03) and a["fc_rank"] == 2
    assert a["name"] == "Alfa S.A."
    # the market page lists tradable stocks only, and B is thin
    assert [r["isin"] for r in tool.market["surprised"]] == [A]


def test_slugs_are_unique() -> None:
    taken: set[str] = set()
    assert stock_export.slugify("PKN Orlen", "PL1", taken) == "pkn-orlen"
    assert stock_export.slugify("PKN-ORLEN", "PL2", taken) == "pkn-orlen-pl2"
    assert stock_export.slugify(None, "PL3", taken) == "pl3"
