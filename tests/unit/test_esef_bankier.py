# ruff: noqa: E501  (inline XBRL and listing fixtures are kept on one line each)
"""Annual reports from Bankier's copies: finding them, choosing the file, reading inline XBRL."""

import io
import json
import zipfile
from datetime import UTC, date, datetime

import httpx
import polars as pl
import pytest

from undercurrent import esef, lake
from undercurrent import esef_bankier as b
from undercurrent.fetch import PoliteClient

LEI = "259400TESTLEI0000001"


def _item(when: str, href: str, title: str) -> str:
    return (
        f'<li><span class="m-quotes-announcements-item__date">{when}</span>'
        f'<a class="m-quotes-announcements-item__anchor" href="{href}">{title}</a></li>'
    )


LISTING = "".join(
    [
        _item("23.03.2026 19:00", "https://www.bankier.pl/wiadomosc/ABC-Wyniki-finansowe-SRR-2025-200.html", "Wyniki finansowe SRR /2025"),
        _item("23.03.2026 18:55", "https://www.bankier.pl/wiadomosc/ABC-Wyniki-finansowe-RR-2025-199.html", "Wyniki finansowe RR /2025"),
        _item("20.11.2025 18:00", "https://www.bankier.pl/wiadomosc/ABC-Wyniki-finansowe-QSr-3-2025-150.html", "Wyniki finansowe QSr 3/2025"),
        _item("25.03.2025 19:41", "https://www.bankier.pl/wiadomosc/ABC-wyniki-finansowe-100.html", "wyniki finansowe"),
        _item("10.08.2024 12:00", "https://www.bankier.pl/wiadomosc/ABC-Zwolanie-WZ-50.html", "Zwołanie WZ"),
    ]
)  # fmt: skip

IXBRL = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
<body><ix:header><ix:resources>
 <xbrli:context id="d"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">{LEI}</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="i"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">{LEI}</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:context id="seg"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">{LEI}</xbrli:identifier>
  <xbrli:segment><xbrldi:explicitMember dimension="x:Y">x:Z</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:unit id="pln"><xbrli:measure>iso4217:PLN</xbrli:measure></xbrli:unit>
 <xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:PLN</xbrli:measure></xbrli:unitNumerator>
  <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
</ix:resources></ix:header>
<p><ix:nonFraction name="ifrs:Revenue" contextRef="d" unitRef="pln" decimals="-3" scale="3" format="ixt5:num-comma-decimal">551 879</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:CashFlowsFromUsedInOperatingActivities" contextRef="d" unitRef="pln" decimals="-3" scale="3" sign="-" format="ixt5:num-comma-decimal">1 234,5</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:Assets" contextRef="i" unitRef="pln" decimals="-3" scale="3" format="ixt:num-dot-decimal">1,353,048</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:Assets" contextRef="seg" unitRef="pln" decimals="-3" scale="3">7</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:LeaseLiabilities" contextRef="i" unitRef="pln" decimals="0" format="ixt5:fixed-zero">-</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:BasicEarningsLossPerShare" contextRef="d" unitRef="eps" decimals="2" format="ixt5:num-comma-decimal">4,65</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:Equity" contextRef="i" unitRef="pln" decimals="-3" scale="3" format="ixt5:num-comma-decimal">1 142 222</ix:nonFraction></p>
<p><ix:nonFraction name="ifrs:ProfitLoss" contextRef="d" unitRef="pln" decimals="-3" scale="3" format="ixt5:num-comma-decimal">197 615</ix:nonFraction></p>
</body></html>""".encode()


def test_listing_finds_typed_and_untitled_results_pages():
    found, oldest = b.parse_listing(LISTING)
    assert [(r.kind, r.fiscal_year) for r in found] == [("SRR", 2025), ("RR", 2025), (None, None)]
    assert oldest == datetime(2024, 8, 10, 10, 0, tzinfo=UTC)
    assert found[0].published == datetime(2026, 3, 23, 18, 0, tzinfo=UTC)
    page = "<p>Raport roczny RR 2024 (rok) (zgodnie z § 60)</p>"
    assert b.classify(found[2], page).fiscal_year == 2024
    assert b.classify(found[2], "<p>Raport kwartalny QSr 1/2025</p>").kind is None
    assert [r.kind for r in b.pick(found)] == ["SRR"]


def test_report_files_prefer_esef_packages():
    att = "https://bonnier.pl/static/att/emitent/2026-03/20260325_171917_0296124026_"
    html = "".join(
        f'<a href="{att}{n}">x</a>'
        for n in [
            "SzA_GK_GPW-2025-12-31-1-pl.xhtml",
            "List_Prezesa_Zarzadu.xhtml",
            "ConsolidatedFinancialStatementsfor2025.xhtml",
            "abc-2025-12-31-1-pl.xhtml",
            "G30CO71KTT9JDYJESN22-2025-12-31-1-pl.xbri",
            "gpw-2024-12-31-0-pl_202503250845971304.zip",
            "gpw-2024-12-31-0-pl_202503250845971304.zip.xades",
            "Raport.pdf",
        ]
    )
    names = [u.rsplit("/", 1)[-1] for u in b.report_files(html, "https://www.bankier.pl/x.html")]
    assert names[0].endswith("G30CO71KTT9JDYJESN22-2025-12-31-1-pl.xbri")
    assert names[1].endswith("gpw-2024-12-31-0-pl_202503250845971304.zip")
    assert names[2].endswith("abc-2025-12-31-1-pl.xhtml")
    assert names[3].endswith("ConsolidatedFinancialStatementsfor2025.xhtml") and len(names) == 4


@pytest.mark.parametrize(
    ("text", "fmt", "value"),
    [
        ("1 234,5", "ixt5:num-comma-decimal", 1234.5),
        ("1.234,5", "ixt:num-comma-decimal", 1234.5),
        ("1,234.5", "ixt:num-dot-decimal", 1234.5),
        ("-", "ixt5:fixed-zero", 0.0),
        ("abc", "ixt:num-dot-decimal", None),
    ],
)
def test_numbers(text, fmt, value):
    assert b._number(text, fmt) == value


def test_inline_xbrl_facts():
    known = datetime(2026, 3, 23, 18, tzinfo=UTC)
    lei, facts = b.facts_from_ixbrl(IXBRL, "bankier-200", known)
    assert lei == LEI
    rows = {r["concept"]: r for r in facts.iter_rows(named=True)}
    assert rows["revenue"]["value"] == 551_879_000
    assert rows["revenue"]["period_start"] == date(2025, 1, 1)
    assert rows["operating_cash_flow"]["value"] == -1_234_500
    assert rows["assets"]["value"] == 1_353_048_000  # the segment's 7 is left out
    assert rows["lease_liabilities"]["value"] == 0
    assert rows["eps_basic"]["unit"] == "PLN/xbrli:shares" and rows["eps_basic"]["value"] == 4.65
    assert set(facts["fiscal_period_end"].to_list()) == {date(2025, 12, 31)}
    assert set(facts["known_at"].to_list()) == {known}


def _package(doc: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("abc-2025-12-31-1-pl/META-INF/catalog.xml", "<x/>")
        z.writestr("abc-2025-12-31-1-pl/reports/abc-2025-12-31-1-pl.xhtml", doc)
    return out.getvalue()


def test_report_from_package_or_bare():
    assert b.report_from_package(_package(IXBRL)) == IXBRL
    assert b.report_from_package(IXBRL) == IXBRL
    assert b.report_from_package(b"<html>a rendered copy</html>") is None


def _client(tmp_path, gleif: dict[str, list[str]]) -> PoliteClient:
    page = (
        '<p>Raport roczny SRR 2025</p><a href="https://bonnier.pl/static/att/emitent/2026-03/'
        '20260323_190000_1_abc-2025-12-31-1-pl.zip">zip</a>'
    )

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/komunikaty" in url:
            return httpx.Response(200, text=LISTING)
        if "bankier.pl/wiadomosc/" in url:
            return httpx.Response(200, text=page)
        if "bonnier.pl" in url:
            return httpx.Response(200, content=_package(IXBRL))
        if "gleif" in url:
            data = [{"attributes": {"isin": i}} for i in gleif.get(LEI, [])]
            return httpx.Response(200, text=json.dumps({"data": data}))
        return httpx.Response(404)

    return PoliteClient(
        tmp_path / "cache", min_interval_s=0, max_jitter_s=0, transport=httpx.MockTransport(handler)
    )


def test_ingest_keeps_facts_when_gleif_lists_only_bonds(tmp_path):
    co = [b.Company("PLABC0000001", "ABC")]
    with _client(tmp_path, {LEI: ["PL0GO0000001"]}) as c:
        out = b.ingest(c, tmp_path, co, now=datetime(2026, 11, 17, tzinfo=UTC))
    assert out["ok"] == 1 and out["facts"] == 7
    links = lake.read_state(tmp_path, esef.LEI_STATE)
    assert links is not None and (
        "PLABC0000001" in links.filter(pl.col("lei") == LEI)["isin"].to_list()
    )
    facts = esef.fundamentals_as_of(tmp_path, datetime(2026, 12, 1, tzinfo=UTC))
    assert facts.filter(pl.col("isin") == "PLABC0000001").height == 7
    # nothing is due again within a month, and the pages are not read twice
    with _client(tmp_path, {}) as c:
        assert b.ingest(c, tmp_path, co, now=datetime(2026, 11, 20, tzinfo=UTC))["companies"] == 0


def test_ingest_refuses_another_listed_issuers_report(tmp_path):
    co = [b.Company("PLABC0000001", "ABC"), b.Company("PLXYZ0000001", "XYZ")]
    with _client(tmp_path, {LEI: ["PLXYZ0000001"]}) as c:
        b.ingest(
            c, tmp_path, co[:1] + co[1:], max_companies=1, now=datetime(2026, 11, 17, tzinfo=UTC)
        )
    state = lake.read_state(tmp_path, b.STATE)
    assert state is not None
    assert "other_issuer" in state["status"].to_list()
    assert "ok" not in state["status"].to_list()
    assert not (tmp_path / "bronze" / esef.TABLE).exists()
