"""ESEF fundamentals: fact extraction, the resumable ingest, the point-in-time view."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from undercurrent import esef, lake
from undercurrent.fetch import PoliteClient

FIXTURE = Path(__file__).parents[1] / "fixtures" / "esef" / "filing_pl_2024-03-31.json"
LEI = "2594007GQP5TDYY9OD45"
FILING = esef.Filing(
    "16090", LEI, date(2024, 3, 31), datetime(2024, 7, 30, 11, 26, tzinfo=UTC), "x", 0
)


def test_headline_facts_are_extracted_and_decoys_dropped():
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    df = esef.facts_from_json(doc, FILING)
    cur = {
        r["concept"]: r["value"]
        for r in df.filter(pl.col("period_end") == date(2024, 3, 31)).iter_rows(named=True)
    }
    assert cur["revenue"] == 1_186_121_000
    assert cur["net_profit_parent"] == 6_210_000
    assert cur["assets"] == 501_484_000
    assert cur["equity_parent"] == 149_723_000
    assert cur["operating_cash_flow"] == 43_439_000
    # the fiscal year runs April to March: the period is read from the facts, not assumed
    rev = df.filter((pl.col("concept") == "revenue") & (pl.col("period_end") == date(2024, 3, 31)))
    assert rev["period_start"][0] == date(2023, 4, 1)
    # the prior year's comparatives are kept too
    assert date(2023, 3, 31) in set(df["period_end"])
    # no duplicates, no dimensional breakdowns, no text, no share counts
    assert df["fact_key"].n_unique() == df.height
    assert set(df["unit"]) <= {"PLN", "PLN/xbrli:shares"}
    assert (df["known_at"] == FILING.added).all()


def _transport(calls: list[str]):
    doc = FIXTURE.read_text(encoding="utf-8")

    def filing(i: int, added: str) -> dict:
        return {
            "type": "filing",
            "id": str(i),
            "attributes": {
                "period_end": "2024-03-31",
                "country": "PL",
                "fxo_id": f"{LEI}-2024-03-31-ESEF-PL-{i}",
                "date_added": added,
                "json_url": f"/{LEI}/f{i}.json",
                "error_count": 0,
            },
        }

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        url = str(req.url)
        if "/api/filings" in url and "page%5Bnumber%5D=2" in url:
            return httpx.Response(
                200, json={"data": [filing(2, "2024-08-01 09:00:00")], "links": {}}
            )
        if "/api/filings" in url:
            nxt = "/api/filings?filter%5Bcountry%5D=PL&page%5Bnumber%5D=2"
            return httpx.Response(
                200, json={"data": [filing(1, "2024-07-30 11:26:50")], "links": {"next": nxt}}
            )
        if url.endswith(".json"):
            return httpx.Response(200, text=doc)
        if "gleif" in url:
            return httpx.Response(200, json={"data": [{"attributes": {"isin": "PLGRODN00015"}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_ingest_reads_each_filing_once_and_maps_leis(tmp_path):
    calls: list[str] = []
    client = PoliteClient(tmp_path / "cache", transport=_transport(calls), sleep=lambda s: None)
    now = datetime(2026, 11, 12, tzinfo=UTC)
    first = esef.ingest(client, tmp_path, max_filings=1, now=now)
    assert first["filings"] == 1 and first["facts"] > 10 and first["isin_links"] == 1
    second = esef.ingest(client, tmp_path, max_filings=5, now=now)
    assert second["filings"] == 1 and second["isin_links"] == 0  # the LEI is known already
    third = esef.ingest(client, tmp_path, max_filings=5, now=now)
    assert third["filings"] == 0
    assert sum(u.endswith(".json") for u in calls) == 2  # never fetched twice
    done = lake.read_state(tmp_path, esef.FILINGS_STATE)
    assert done is not None and sorted(done["filing_id"].to_list()) == ["1", "2"]


def test_fundamentals_are_point_in_time(tmp_path):
    calls: list[str] = []
    client = PoliteClient(tmp_path / "cache", transport=_transport(calls), sleep=lambda s: None)
    esef.ingest(client, tmp_path, max_filings=5, now=datetime(2026, 11, 12, tzinfo=UTC))
    before = esef.fundamentals_as_of(tmp_path, datetime(2024, 7, 1, tzinfo=UTC))
    assert before.height == 0  # nothing was in the repository yet
    mid = esef.fundamentals_as_of(tmp_path, datetime(2024, 7, 31, tzinfo=UTC))
    assert set(mid["filing_id"]) == {"1"} and set(mid["isin"]) == {"PLGRODN00015"}
    later = esef.fundamentals_as_of(tmp_path, datetime(2025, 1, 1, tzinfo=UTC))
    assert set(later["filing_id"]) == {"1", "2"}
    assert (later["known_at"] <= datetime(2025, 1, 1, tzinfo=UTC)).all()


def test_no_lake_no_facts(tmp_path):
    df = esef.fundamentals_as_of(tmp_path, datetime(2025, 1, 1, tzinfo=UTC))
    assert df.height == 0 and "isin" in df.columns


@pytest.mark.parametrize("value", ["n/a", None, float("nan")])
def test_non_numeric_values_are_skipped(value):
    doc = {
        "facts": {
            "a": {
                "value": value,
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": f"scheme:{LEI}",
                    "period": "2023-01-01T00:00:00/2024-01-01T00:00:00",
                    "unit": "iso4217:PLN",
                },
            }
        }
    }
    assert esef.facts_from_json(doc, FILING).height == 0
