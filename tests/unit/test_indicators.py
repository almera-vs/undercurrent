"""GPW's indicators page: parsed from a real copy, landed once per session."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from undercurrent import indicators, lake

PAGE = (
    Path(__file__).parents[1] / "fixtures" / "indicators" / "gpw_wskazniki_2026-11-15.html"
).read_text(encoding="utf-8")
DAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)


def test_rows_of_both_tables() -> None:
    df = indicators.parse(PAGE, DAY, NOW)
    assert df.height == 8
    assert set(df["listing"]) == {"domestic", "foreign"}
    r = df.filter(df["ticker"] == "11BIT").row(0, named=True)
    assert r["isin"] == "PL11BTS00015"
    assert r["sector_code"] == 650 and r["sector"] == "Handel i usługi - Gry"
    assert r["shares"] == 2_417_199
    assert r["market_value_pln"] == pytest.approx(268.31e6)
    assert r["book_value_pln"] == pytest.approx(235.75e6)
    assert r["report_date"] == date(2026, 6, 30) and r["report_type"] == "J"
    assert r["pbv"] == pytest.approx(1.14)
    assert r["pe"] is None  # "x": a loss
    assert r["dividend_yield"] is None  # "--"
    g = df.filter(df["ticker"] == "3RGAMES").row(0, named=True)
    assert g["pe"] == pytest.approx(12.5) and g["dividend_yield"] == pytest.approx(0.063)


def test_ingest_lands_one_partition_per_session(tmp_path) -> None:
    class Client:
        calls = 0

        def get(self, url: str, **kw: object) -> object:
            Client.calls += 1

            class Res:
                @staticmethod
                def text() -> str:
                    return PAGE

            return Res()

    assert indicators.ingest(Client(), tmp_path, DAY, now=NOW) == 8  # type: ignore[arg-type]
    assert indicators.ingest(Client(), tmp_path, DAY, now=NOW) == 0  # type: ignore[arg-type]
    assert Client.calls == 1
    df = indicators.latest(tmp_path, DAY)
    assert df is not None and df.height == 8
    assert indicators.latest(tmp_path, date(2026, 11, 12)).height == 0  # type: ignore[union-attr]
    assert lake.partition_exists(tmp_path, "bronze", indicators.TABLE, DAY)


def test_a_changed_layout_fails_loudly(tmp_path) -> None:
    class Client:
        def get(self, url: str, **kw: object) -> object:
            class Res:
                @staticmethod
                def text() -> str:
                    return "<html><body>maintenance</body></html>"

            return Res()

    with pytest.raises(ValueError, match="layout"):
        indicators.ingest(Client(), tmp_path, DAY, now=NOW)  # type: ignore[arg-type]
