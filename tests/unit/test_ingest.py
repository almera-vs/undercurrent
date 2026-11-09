from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from undercurrent import lake
from undercurrent.fetch import PoliteClient
from undercurrent.ingest import (
    NoSessionError,
    extract_report_page,
    ingest_gpw_prices,
    ingest_reports,
    parse_gpw_xls,
    parse_listing,
    parse_nc_daily,
)

FIX = Path(__file__).parents[1] / "fixtures"
DAY = date(2026, 11, 6)


def _page(name: str) -> str:
    return (FIX / "pages" / name).read_text(encoding="utf-8")


def test_parse_listing_gpw():
    rows = parse_listing((FIX / "listings" / "gpw_2026-11-06.html").read_text("utf-8"), "gpw")
    assert len(rows) == 41
    first = rows[0]
    assert first.report_id == "gpw:500187"
    assert first.published_at_local == datetime(2026, 11, 6, 20, 55, 52)
    assert first.kind == "Bieżący" and first.system == "ESPI"
    assert first.report_number == "29/2026"
    assert first.isin == "PLJRINV00013"
    assert first.issuer_name.startswith("JRH ALTERNATYWNA")
    assert first.title is not None and first.title.startswith("Podpisanie umowy ramowej")
    assert len({r.geru_id for r in rows}) == 41


def test_parse_listing_nc_and_2015():
    nc = parse_listing((FIX / "listings" / "nc_2026-11-06.html").read_text("utf-8"), "nc")
    assert len(nc) == 47
    assert {r.system for r in nc} == {"EBI", "ESPI"}
    old = parse_listing((FIX / "listings" / "gpw_2015-01-05.html").read_text("utf-8"), "gpw")
    assert old and all(r.published_at_local.year == 2015 for r in old)


def test_extract_espi_legacy_holder_threshold():
    p = extract_report_page(_page("gpw_500236_espi_legacy.html"))
    assert p.template == "espi_legacy"
    assert p.subject == "Informacja o transakcjach na akcjach KRUK S.A."
    assert p.legal_basis is not None and p.legal_basis.startswith("Art. 70 pkt 1 Ustawy o ofercie")
    assert p.body_text is not None and "Goldman Sachs" in p.body_text
    assert "MESSAGE" not in p.body_text
    assert len(p.attachments) == 1 and p.attachments[0]["href"].startswith("attachment/")


def test_extract_espi_legacy_insider():
    p = extract_report_page(_page("gpw_500131_espi_legacy_insider.html"))
    assert p.legal_basis is not None and p.legal_basis.startswith(
        "Art. 19 ust. 3 Rozporządzenia MAR"
    )
    assert p.body_text is not None and "Wiceprezesa Zarządu" in p.body_text


def test_extract_gpwdata_template():
    p = extract_report_page(_page("nc_244783_gpwdata.html"))
    assert p.template == "gpwdata"
    assert p.legal_basis == "§ 4 ust. 2 Załącznika nr 3 do Regulaminu ASO"
    assert p.body_text is None
    assert any("PDF" in n for n in p.notes)
    assert len(p.attachments) == 5


def test_extract_ebi_legacy_template():
    p = extract_report_page(_page("nc_166637_ebi_legacy.html"))
    assert p.template == "ebi_legacy"
    assert p.legal_basis is not None and p.legal_basis.startswith("§ 6 ust. 14.2")
    assert p.body_text is not None and p.body_text.startswith("Zarząd Bit Evil")


def test_extract_unknown_template():
    p = extract_report_page("<html><body><p>nothing</p></body></html>")
    assert p.template == "unknown" and p.legal_basis is None


def test_parse_gpw_shares_and_indices():
    shares = parse_gpw_xls((FIX / "prices" / "gpw_shares_2026-11-06.xls").read_bytes(), DAY)
    assert shares.height == 402
    row = shares.filter(pl.col("isin") == "PLNFI0600010").row(0, named=True)
    assert row["close"] == 2.23 and row["volume"] == 2368 and row["trade_date"] == DAY
    idx = parse_gpw_xls((FIX / "prices" / "gpw_indices_2026-11-06.xls").read_bytes(), DAY)
    wig = idx.filter(pl.col("name") == "WIG").row(0, named=True)
    assert wig["close"] == pytest.approx(156043.30)


def test_parse_gpw_rejects_wrong_day():
    with pytest.raises(ValueError):
        parse_gpw_xls(
            (FIX / "prices" / "gpw_shares_2026-11-06.xls").read_bytes(), date(2026, 11, 5)
        )


def test_parse_nc_xlsx():
    df = parse_nc_daily((FIX / "prices" / "nc_2026-11-06.xlsx").read_bytes(), DAY, xlsx=True)
    assert df.height > 300
    one = df.filter(pl.col("isin") == "PLONESL00011").row(0, named=True)
    assert one["ticker"] == "ONE"
    assert one["close"] == pytest.approx(0.0974)
    assert one["reference"] == pytest.approx(0.0976)
    assert one["volume"] == 2609 and one["trades"] == 6


def test_parse_nc_legacy_xls():
    df = parse_nc_daily(
        (FIX / "prices" / "nc_2015-01-30.xls").read_bytes(), date(2015, 1, 30), xlsx=False
    )
    assert df.height > 300
    assert {"isin", "ticker", "close", "reference", "volume"} <= set(df.columns)
    assert df["close"].null_count() < df.height


# ---------------------------------------------------------------- end-to-end with a fake site


def _fake_site(calls: list[str]):
    listing = {
        "gpw": (FIX / "listings" / "gpw_2026-11-06.html").read_text("utf-8"),
        "nc": (FIX / "listings" / "nc_2026-11-06.html").read_text("utf-8"),
    }
    page = _page("gpw_500131_espi_legacy_insider.html")
    shares = (FIX / "prices" / "gpw_shares_2026-11-06.xls").read_bytes()
    idx = (FIX / "prices" / "gpw_indices_2026-11-06.xls").read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        site = "nc" if "newconnect" in req.url.host else "gpw"
        if req.url.path == "/ajaxindex.php":
            body = req.content.decode()
            return httpx.Response(200, text=listing[site] if "offset=0&" in body else "")
        if req.url.path == "/komunikat":
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if req.url.path == "/archiwum-notowan":
            data = idx if "type=1&" in str(req.url) else shares
            return httpx.Response(
                200, content=data, headers={"content-type": "application/vnd.ms-excel"}
            )
        return httpx.Response(404)

    return handler


def test_ingest_reports_is_idempotent(tmp_path):
    calls: list[str] = []
    now = datetime(2026, 11, 6, 18, 0, tzinfo=UTC)
    with PoliteClient(
        tmp_path / "cache",
        min_interval_s=0,
        max_jitter_s=0,
        transport=httpx.MockTransport(_fake_site(calls)),
    ) as c:
        s1 = ingest_reports(c, tmp_path / "lake", DAY, now=now)
        n_calls = len(calls)
        s2 = ingest_reports(c, tmp_path / "lake", DAY, now=now)
    assert s1.listed == 88 and s1.written == 88
    assert s2.new == 0 and s2.written == 0
    assert len(calls) - n_calls == 2  # only the two listing requests
    parts = lake.partition_parts(tmp_path / "lake", "bronze", "reports", DAY)
    assert [p.name for p in parts] == ["part-0.parquet"]
    df = lake.read_partition(tmp_path / "lake", "bronze", "reports", DAY)
    assert df is not None and df["report_id"].is_unique().all()
    assert (df["known_at"] == df["published_at"]).all()
    current = df.filter(pl.col("kind") == "Bieżący")
    assert current["legal_basis"].null_count() == 0
    assert df.filter(pl.col("kind") != "Bieżący")["template"].unique().to_list() == ["listing_only"]


def test_ingest_prices_idempotent(tmp_path):
    calls: list[str] = []
    with PoliteClient(
        tmp_path / "cache",
        min_interval_s=0,
        max_jitter_s=0,
        transport=httpx.MockTransport(_fake_site(calls)),
    ) as c:
        first = ingest_gpw_prices(c, tmp_path / "lake", DAY)
        second = ingest_gpw_prices(c, tmp_path / "lake", DAY)
    assert first == {"prices_gpw": 402, "indices_gpw": 56}
    assert second == {"prices_gpw": 0, "indices_gpw": 0}
    assert len(calls) == 2


def test_no_session_detected(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<!DOCTYPE html><html></html>", headers={"content-type": "text/html"}
        )

    with (
        PoliteClient(
            tmp_path / "cache",
            min_interval_s=0,
            max_jitter_s=0,
            transport=httpx.MockTransport(handler),
        ) as c,
        pytest.raises(NoSessionError),
    ):
        ingest_gpw_prices(c, tmp_path / "lake", date(2026, 11, 8))
