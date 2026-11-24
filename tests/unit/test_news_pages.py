"""The news parsers on trimmed copies of real listing pages (tests/fixtures/news).

The copies were fetched on 2026-11-24 for XTB; images, scripts and teasers are removed and a
pseudonym in one headline is replaced by a fictional name. Live pages: tests/network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from undercurrent import news
from undercurrent.news_common import CompanyInfo

FIXTURES = Path(__file__).parents[1] / "fixtures" / "news"
NOW = datetime(2026, 11, 24, 19, 0, tzinfo=UTC)
XTB = CompanyInfo("PLXTRDM00011", "XTB", "XTB").company()


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_biznesradar_page_parses_every_record_with_outlet_and_warsaw_time() -> None:
    html = _page("biznesradar_XTB_1.html")
    page = news.parse_biznesradar(html, isin=XTB.isin, fetched_at=NOW, base="https://x.test/")
    assert page.outcome is news.Outcome.OK and len(page.items) == 8
    first = page.items[0]
    assert first.published_at == datetime(2026, 11, 20, 8, 44, 8, tzinfo=UTC)  # 09:44:08 Warsaw
    assert first.outlet == "ISBiznes.pl" and first.title.startswith("XTB miało 213,65 mln zł")
    assert first.url.startswith("https://isbiznes.pl/")
    assert news.page_identity_ok(html, news.BIZNESRADAR, XTB)


def test_biznesradar_own_copy_of_a_filing_is_a_reprint() -> None:
    html = _page("biznesradar_XTB_1.html")
    page = news.parse_biznesradar(html, isin=XTB.isin, fetched_at=NOW, base="https://x.test/")
    copy = page.items[1]
    assert copy.outlet == "BiznesRadar" and "Raport okresowy" in copy.title
    v = news.verdict(copy, XTB, require_mention=False)
    assert v.reason == "excluded:filing_reprint"


def test_biznesradar_page_title_names_the_company_with_or_without_a_code() -> None:
    xtb = news._BR_TITLE.search(_page("biznesradar_XTB_1.html"))
    assert xtb is not None and xtb.group(1) == "XTB"
    att = news._BR_TITLE.search("<title>Wiadomości GRUPA AZOTY (ATT)- BiznesRadar.pl</title>")
    assert att is not None and att.groups() == ("GRUPA AZOTY", "ATT")
    dash = news._BR_TITLE.search("<title>Wiadomości MO-BRUK (MOBRUK)- BiznesRadar.pl</title>")
    assert dash is not None and dash.group(1) == "MO-BRUK"


def test_bankier_page_parses_time_title_and_skips_the_section_label() -> None:
    html = _page("bankier_XTB_1.html")
    page = news.parse_bankier(html, isin=XTB.isin, fetched_at=NOW, base="https://www.bankier.pl/")
    assert page.outcome is news.Outcome.OK and len(page.items) == 8
    it = page.items[1]
    assert it.published_at == datetime(2026, 11, 18, 15, 32, tzinfo=UTC)  # 16:32 Warsaw
    assert it.title == "XTB punktuje KNF po krachu akcji: to uderza w inwestorów"
    assert it.url.startswith("https://www.bankier.pl/wiadomosc/")
    assert not page.items[0].title.startswith("SCAMMING")  # the label above the headline
    assert news.page_identity_ok(html, news.BANKIER, XTB)


def test_real_headlines_get_the_verdicts_they_should() -> None:
    br = news.parse_biznesradar(
        _page("biznesradar_XTB_1.html"), isin=XTB.isin, fetched_at=NOW, base="https://x.test/"
    ).items
    bk = news.parse_bankier(
        _page("bankier_XTB_1.html"), isin=XTB.isin, fetched_at=NOW, base="https://x.test/"
    ).items
    reasons = {i.title[:30]: news.verdict(i, XTB, require_mention=False).reason for i in br + bk}
    assert reasons["Mapa GPW: XTB, KGHM, Allegro, "] == "excluded:market_wrap"
    assert reasons["Kolejny czerwony dzień na wars"] == "excluded:market_wrap"
    assert reasons["Kurs XTB spada o 5,1 proc. do "] == "excluded:price_line"
    assert reasons["XTB miało 213,65 mln zł zysku "] == "match"


def test_google_feed_strips_the_outlet_and_keeps_its_site() -> None:
    page = news.parse_google_rss(
        (FIXTURES / "google_XTB.xml").read_bytes(), isin=XTB.isin, fetched_at=NOW
    )
    assert page.outcome is news.Outcome.OK and len(page.items) == 12
    first = page.items[0]
    assert first.title == "KNF na nowo ureguluje CFD. Akcje XTB mocno w dół"
    assert first.outlet == "Stowarzyszenie Inwestorów Indywidualnych"
    assert first.outlet_url == "https://www.sii.org.pl"
    assert first.published_at == datetime(2026, 11, 17, 12, 14, tzinfo=UTC)
    assert all(i.url.startswith("https://news.google.com/") for i in page.items)
