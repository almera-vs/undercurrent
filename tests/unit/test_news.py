"""News fetcher: parsers, symbol check, matching exclusions, throttling and page walking.

Fixtures use fictional companies (Alfa Testowa, ticker ALFA) and inline markup written from
the listing structure described during verification, not from saved live pages.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from undercurrent import news
from undercurrent.fetch import SourceBlockedError
from undercurrent.news import (
    BANKIER,
    BIZNESRADAR,
    Company,
    HostThrottle,
    NewsItem,
    Outcome,
    fetch_company,
    fetch_google,
    isin_on_page,
    parse_bankier,
    parse_biznesradar,
    parse_google_rss,
    unique,
    verdict,
)

ISIN = "PLTEST000001"
COMPANY = Company(
    isin=ISIN, names=("Alfa Testowa", "ALFA"), ticker="ALFA", own_domains=("alfa.example",)
)
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
BASE = "https://www.biznesradar.pl/wiadomosci/ALFA,1"


class Resp:
    def __init__(self, body: str) -> None:
        self._body = body
        self.status = 200

    def text(self) -> str:
        return self._body


class Stub:
    """A PoliteClient stand-in: maps URL -> body, exception, or a list consumed in order."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> Resp:
        self.calls.append(url)
        val = self.routes.get(url, "")
        if isinstance(val, list):
            val = val.pop(0)
        if isinstance(val, BaseException):
            raise val
        return Resp(str(val))


def _status(url: str, code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"{code}", request=req, response=httpx.Response(code, headers=headers, request=req)
    )


BR_PAGE = """<html><body><div id="news-radar-body">
<div id="msg-1114566117" class="record record-type-NEWS">
  <div class="record-avatar"><img src="/gfx/logos/oid_1.jpg" alt="ISBiznes.pl" /></div>
  <div class="record-header">
    <a target="_blank"
       href="https://isbiznes.example/alfa-prognozy/"
       rel="nofollow">Alfa Testowa podnosi prognozy na 2027 rok</a>
  </div>
  <div class="record-body"></div>
  <div class="record-footer">
    <a target="_blank" class="record-author" href="https://feed.example/x.xml">ISBiznes.pl</a>
    <span>&nbsp;|&nbsp;</span><span class="record-date">2026-11-20 09:44:08</span>
    <span>&nbsp;|&nbsp;Giełda</span>
    <a href="javascript:void(0)" class="record-recommend">Polecam!</a>
  </div>
</div>
<div id="msg-171279" class="record record-type-POST">
  <div class="record-avatar"> </div>
  <div class="record-header">
    <a href="/a/171279,alfa-testowa-raport-kwartalny-3-2026"
       rel="nofollow">Alfa Testowa SA Raport kwartalny 3/2026</a>
  </div>
  <div class="record-footer"><span class="record-date">2026-11-20 00:04:38</span></div>
</div>
</div>
<table class="profileSummary"><tr><th>ISIN:</th><td>PLTEST000001</td></tr></table>
</body></html>"""

BANK_PAGE = """<html><body><ul>
<li><a href="/wiadomosc/Alfa-Testowa-traci-po-wynikach-9231029.html">
<img src="https://galeria.bankier.pl/x.jpg"><span>2026-11-18 16:32</span>
<h3>Alfa Testowa traci po wynikach kwartalnych</h3><p>Opis.</p></a></li>
</ul>
<div>ISIN PLTEST000001</div></body></html>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Alfa Testowa dostaje zgodę na fuzję - Parkiet</title>
<link>https://news.google.com/rss/articles/AAA?oc=5</link>
<pubDate>Tue, 17 Nov 2026 12:14:00 GMT</pubDate>
<source url="https://www.parkiet.com">Parkiet</source></item>
<item><title>Alfa Testowa notowania - serwis</title>
<link>https://news.google.com/rss/articles/BBB?oc=5</link>
<pubDate>Tue, 17 Nov 2026 06:00:00 GMT</pubDate>
<source url="https://example.org">Serwis</source></item>
</channel></rss>"""


# ---- parsers ----------------------------------------------------------------------------


def test_biznesradar_parses_headline_outlet_and_warsaw_time() -> None:
    page = parse_biznesradar(BR_PAGE, isin=ISIN, fetched_at=NOW, base=BASE)
    assert page.outcome is Outcome.OK
    item, post = page.items
    assert item.title == "Alfa Testowa podnosi prognozy na 2027 rok"
    assert item.url == "https://isbiznes.example/alfa-prognozy/"
    assert item.outlet == "ISBiznes.pl"  # from record-author, not the recommend link
    # BiznesRadar's own page (record-type-POST): relative link, no author
    assert post.url == "https://www.biznesradar.pl/a/171279,alfa-testowa-raport-kwartalny-3-2026"
    assert post.outlet == "BiznesRadar"
    # 2026-11-20 09:44:08 Warsaw (CET, UTC+1) is 08:44:08 UTC
    assert item.published_at == datetime(2026, 11, 20, 8, 44, 8, tzinfo=UTC)
    assert item.precision == "minute"


def test_bankier_parses_time_line_and_headline() -> None:
    page = parse_bankier(BANK_PAGE, isin=ISIN, fetched_at=NOW, base="https://www.bankier.pl/x")
    assert page.outcome is Outcome.OK
    (item,) = page.items
    assert item.title == "Alfa Testowa traci po wynikach kwartalnych"
    assert item.published_at == datetime(2026, 11, 18, 15, 32, tzinfo=UTC)
    assert (
        item.url == "https://www.bankier.pl/wiadomosc/Alfa-Testowa-traci-po-wynikach-9231029.html"
    )


def test_google_rss_strips_outlet_suffix_and_keeps_outlet_url() -> None:
    page = parse_google_rss(RSS.encode(), isin=ISIN, fetched_at=NOW)
    assert page.outcome is Outcome.OK
    first, second = page.items
    assert first.title == "Alfa Testowa dostaje zgodę na fuzję"
    assert first.outlet == "Parkiet"
    assert first.precision == "minute"
    assert first.outlet_url == "https://www.parkiet.com"
    # one item alone on a whole hour is a real time, not a placeholder
    assert second.precision == "minute"


def test_google_rss_malformed_is_drift_not_empty() -> None:
    page = parse_google_rss(b"<rss><channel><item>", isin=ISIN, fetched_at=NOW)
    assert page.outcome is Outcome.DRIFT


def test_listing_with_timestamps_but_no_items_is_drift() -> None:
    big = "<html>" + ("<div>x</div>" * 800) + "2026-11-20 09:44:08" + "</html>"
    page = parse_biznesradar(big, isin=ISIN, fetched_at=NOW, base=BASE)
    assert page.outcome is Outcome.DRIFT


def test_isin_on_page_requires_label_and_exact_code() -> None:
    assert isin_on_page(BR_PAGE, ISIN)  # <th>ISIN:</th><td>…</td>
    assert isin_on_page("ISIN: PLTEST000001", ISIN)
    assert not isin_on_page(BR_PAGE, "PLTEST000002")
    assert not isin_on_page("PLTEST0000019 elsewhere", ISIN)  # longer code does not match


# ---- matching and exclusions ------------------------------------------------------------


def _item(title: str, url: str = "https://pb.pl/a") -> NewsItem:
    return NewsItem(ISIN, BIZNESRADAR, "x", NOW, "minute", title, url, NOW)


def test_match_on_name_and_on_ticker() -> None:
    assert verdict(_item("Alfa Testowa podnosi prognozy"), COMPANY).counts


def test_short_ticker_needs_upper_case_form() -> None:
    assert verdict(_item("ALFA rośnie po wynikach"), COMPANY).counts
    assert verdict(_item("alfa romeo sprzedaje auta"), COMPANY).reason == "no_mention"


def test_market_wrap_excluded() -> None:
    v = verdict(_item("Notowania GPW: WIG w górę, Alfa Testowa"), COMPANY)
    assert not v.counts and v.reason == "excluded:market_wrap"


def test_price_line_excluded() -> None:
    v = verdict(_item("Kurs akcji Alfa Testowa spada o 3,4 proc."), COMPANY)
    assert not v.counts and v.reason == "excluded:price_line"


def test_own_company_page_excluded() -> None:
    v = verdict(
        _item("Alfa Testowa - nasze nowe konto", url="https://www.alfa.example/oferta"), COMPANY
    )
    assert not v.counts and v.reason == "excluded:own_page"


def test_multi_company_headline_excluded() -> None:
    title = "Alfa Testowa, Beta Testowa i Gamma Testowa tracą na GPW"
    v = verdict(_item(title), COMPANY, other_names=("Beta Testowa", "Gamma Testowa"))
    assert not v.counts and v.reason == "excluded:multi_company"


def test_non_mention_is_no_mention() -> None:
    assert verdict(_item("Zupełnie inna spółka rośnie"), COMPANY).reason == "no_mention"


# ---- fetching and failure handling ------------------------------------------------------


def test_fetch_company_stops_on_isin_mismatch() -> None:
    url = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    client = Stub({url: BR_PAGE.replace("PLTEST000001", "PLOTHER00001")})
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert res.outcome is Outcome.EMPTY and "not this company" in res.detail


def test_fetch_company_blocked_page_is_reported_not_retried() -> None:
    url = news.BANKIER_URL.format(symbol="ALFA", page=1)
    client = Stub({url: SourceBlockedError("bot page")})
    res = fetch_company(
        client,
        BANKIER,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
    )
    assert res.outcome is Outcome.BLOCKED
    assert client.calls == [url]


def test_rate_limit_waits_retry_after_and_halves_workers() -> None:
    url = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    client = Stub({url: [_status(url, 429, {"retry-after": "7"}), BR_PAGE]})
    slept: list[float] = []
    throttle = HostThrottle(8)
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        throttle=throttle,
        sleep=slept.append,
        now=lambda: NOW,
    )
    assert slept == [7.0]
    assert throttle.limit == 4
    assert res.outcome is Outcome.OK
    assert len(res.items) == 2  # both fixture records


def test_unreachable_after_attempts_gives_unreachable() -> None:
    url = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    client = Stub({url: httpx.ConnectError("reset")})
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
    )
    assert res.outcome is Outcome.UNREACHABLE


def test_http_404_is_http_error() -> None:
    url = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    client = Stub({url: _status(url, 404)})
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
    )
    assert res.outcome is Outcome.HTTP_ERROR


def test_pagination_stops_when_a_page_adds_nothing_new() -> None:
    p1 = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    p2 = news.BIZNESRADAR_URL.format(symbol="ALFA", page=2)
    client = Stub({p1: BR_PAGE, p2: BR_PAGE})  # page 2 repeats page 1
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert len(res.items) == 2  # both fixture records
    assert client.calls == [p1, p2]


def test_window_edges_drop_items_outside_since_until() -> None:
    p1 = news.BIZNESRADAR_URL.format(symbol="ALFA", page=1)
    client = Stub({p1: BR_PAGE})
    res = fetch_company(
        client,
        BIZNESRADAR,
        COMPANY,
        since=datetime(2026, 11, 21, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert res.items == []
    assert res.outcome is Outcome.EMPTY


def test_google_fetch_filters_window() -> None:
    url_part = "news.google.com/rss/search"

    class G(Stub):
        def get(self, url: str, **kw: object) -> Resp:
            assert url_part in url
            return Resp(RSS)

    res = fetch_google(
        G({}),
        COMPANY,
        since=datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
        until=datetime(2026, 11, 17, 23, 0, tzinfo=UTC),
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert [i.outlet for i in res.items] == ["Parkiet", "Serwis"]


def test_unique_keeps_one_row_per_source_and_url() -> None:
    a = _item("Alfa Testowa x", url="https://u/1")
    b = NewsItem(
        a.isin,
        a.source,
        a.outlet,
        a.published_at,
        a.precision,
        a.title,
        a.url,
        datetime(2026, 11, 23, tzinfo=UTC),
    )
    assert len(unique([b, a])) == 1
    assert unique([b, a])[0].fetched_at == datetime(
        2026, 11, 23, 0, 0, tzinfo=UTC
    )  # earliest fetch kept


@pytest.mark.parametrize("seconds", [0, 5])
def test_biznesradar_second_resolution(seconds: int) -> None:
    html = BR_PAGE.replace("09:44:08", f"09:44:{seconds:02d}")
    page = parse_biznesradar(html, isin=ISIN, fetched_at=NOW, base=BASE)
    assert page.items[0].published_at.second == seconds


BANK_TITLE = "<title>Wiadomości spółki - Alfa Testowa SA (ALFA) - Giełda - Bankier.pl - 1</title>"


def test_bankier_gate_accepts_matching_ticker_in_title() -> None:
    url = news.BANKIER_URL.format(symbol="ALFA", page=1)
    client = Stub({url: BANK_TITLE + BANK_PAGE})
    res = fetch_company(
        client,
        BANKIER,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert res.outcome is Outcome.OK
    assert len(res.items) == 1


def test_bankier_gate_rejects_other_ticker_in_title() -> None:
    url = news.BANKIER_URL.format(symbol="ALFA", page=1)
    other = BANK_TITLE.replace("(ALFA)", "(BETA)")
    client = Stub({url: other + BANK_PAGE})
    res = fetch_company(
        client,
        BANKIER,
        COMPANY,
        since=datetime(2026, 11, 1, tzinfo=UTC),
        until=NOW,
        sleep=lambda _s: None,
        now=lambda: NOW,
    )
    assert res.outcome is Outcome.EMPTY and res.items == []


def test_price_line_without_akcji_excluded() -> None:
    v = verdict(_item("Kurs ALFA spada o 5,1 proc. do 109,3 zł przy wysokich obrotach"), COMPANY)
    assert not v.counts and v.reason == "excluded:price_line"


def test_list_feature_headline_is_a_wrap() -> None:
    v = verdict(_item("Mapa GPW: ALFA, Beta, Gamma i Delta w centrum uwagi"), COMPANY)
    assert not v.counts and v.reason == "excluded:market_wrap"


def _rss(*items: tuple[str, str]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>https://news.google.com/rss/articles/{n}</link>"
        f'<pubDate>{d}</pubDate><source url="https://pb.example">PB</source></item>'
        for n, (t, d) in enumerate(items)
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def test_google_shared_whole_hour_is_a_date_only_placeholder() -> None:
    feed = _rss(
        ("Alfa Testowa a", "Tue, 17 Nov 2026 08:00:00 GMT"),
        ("Alfa Testowa b", "Tue, 17 Nov 2026 08:00:00 GMT"),
        ("Alfa Testowa c", "Mon, 16 Nov 2026 08:00:00 GMT"),
        ("Alfa Testowa d", "Tue, 17 Nov 2026 12:14:37 GMT"),
        ("Alfa Testowa e", "Tue, 17 Nov 2026 00:00:00 GMT"),
    )
    page = parse_google_rss(feed, isin=ISIN, fetched_at=NOW)
    assert [i.precision for i in page.items] == ["day", "day", "day", "minute", "day"]


def test_own_site_caught_through_google_source_url() -> None:
    item = NewsItem(
        ISIN,
        news.GOOGLE,
        "Alfa",
        NOW,
        "minute",
        "Alfa Testowa - nowa oferta",
        "https://news.google.com/rss/articles/X",
        NOW,
        outlet_url="https://www.alfa.example",
    )
    assert verdict(item, COMPANY).reason == "excluded:own_page"


def test_noise_term_excludes_sponsored_event() -> None:
    co = Company(isin=ISIN, names=("Alfa Testowa",), ticker="ALFA", noise_terms=("KSW",))
    assert verdict(_item("Gala ALFA KSW 123: karta walk"), co).reason == "excluded:noise"
    assert verdict(_item("ALFA traci po decyzji regulatora"), co).counts


def test_google_query_covers_the_until_day() -> None:
    url = news.google_query_url(
        COMPANY,
        since=datetime(2026, 11, 16, 17, 0, tzinfo=UTC),
        until=datetime(2026, 11, 17, 17, 30, tzinfo=UTC),
    )
    assert "after:2026-11-16" in url and "before:2026-11-18" in url


def test_session_diary_headline_is_a_wrap() -> None:
    v = verdict(_item("DZIEŃ NA GPW: korekcyjna sesja; ALFA spada o 15,4 proc."), COMPANY)
    assert not v.counts and v.reason == "excluded:market_wrap"


def test_br_premium_column_is_a_wrap() -> None:
    v = verdict(_item("[BR Premium] Co robią grube ryby: Alfa Testowa w portfelach TFI"), COMPANY)
    assert not v.counts and v.reason == "excluded:market_wrap"


def test_headline_without_an_event_does_not_count() -> None:
    for title in (
        "Alfa Testowa uruchamia automat z pizzą",  # a product note
        "Akcje Alfa Testowa tracą ponad 10 proc.",  # the move, not its cause
        "Alfa Testowa: nowa strategia na lata 2026-2030 w marcu",  # strat- is not strata
    ):
        assert verdict(_item(title), COMPANY).reason == "no_event", title


def test_headline_naming_an_event_counts() -> None:
    for title in (
        "Alfa Testowa miała 12 mln zł straty netto w I kw.",
        "Broker podnosi cenę docelową Alfa Testowa",
        "Alfa Testowa podpisała umowę za 120 mln zł",
        "KNF ogranicza sprzedaż produktów Alfa Testowa",
        "Alfa Testowa rozmawia z globalnym partnerem",
    ):
        assert verdict(_item(title), COMPANY).counts, title


def test_filing_reprint_is_left_to_the_report_path() -> None:
    for title in (
        "BETA BUDOWA S.A. - Złożenie wniosku w sprawie umowy z Alfa Testowa",
        "ALFA TESTOWA ELEKTROWNIA SA (121/2026) POWIADOMIENIE O TRANSAKCJI",
    ):
        v = verdict(_item(title), COMPANY, require_mention=False)
        assert v.reason == "excluded:filing_reprint", title


def test_sport_result_is_not_company_news() -> None:
    v = verdict(_item("Alfa Testowa ZAKSA - Cuprum wynik, kto wygrał mecz"), COMPANY)
    assert v.reason == "excluded:sport"
