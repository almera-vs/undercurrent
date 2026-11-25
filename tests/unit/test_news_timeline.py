"""The big movers' news timeline (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl
import pytest

from undercurrent import lake, news, publish
from undercurrent import news_common as common
from undercurrent import news_timeline as nt
from undercurrent.news import NewsItem, Outcome

ALFA = "PLTEST000001"
BETA = "PLTEST000002"
TUE = date(2026, 11, 17)  # a session; Monday 16 Nov is the previous one
NOW = datetime(2026, 11, 17, 19, 30, tzinfo=UTC)


def _warsaw(m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(2026, m, d, hh, mm, tzinfo=news.WARSAW).astimezone(UTC)


def _info() -> common.CompanyInfo:
    return common.CompanyInfo(ALFA, "ALFA", "Alfa Testowa")


def _names() -> common.NameIndex:
    return common.NameIndex(
        {ALFA: _info().company(), BETA: common.CompanyInfo(BETA, "BETA", "Beta Testowa").company()}
    )


def _item(
    title: str,
    at: datetime,
    source: str = news.BANKIER,
    prec: Literal["minute", "day"] = "minute",
    outlet: str = "Bankier",
) -> NewsItem:
    url = f"https://x.test/{abs(hash((title, at)))}"
    return NewsItem(ALFA, source, outlet, at, prec, title, url, NOW)


def _reports(rows: list[dict[str, Any]]) -> pl.DataFrame:
    base = {
        "report_id": "r",
        "isin": ALFA,
        "system": "ESPI",
        "kind": "Bieżący",
        "signal_type": "other",
        "subtype": None,
        "title": "",
        "subject": None,
        "attachments_json": None,
        "source_url": "https://gpw.test/1",
        "known_at": NOW,
    }
    return pl.DataFrame([{**base, **r} for r in rows] or [base]).head(len(rows))


MOVER = nt.Mover(ALFA, 3.4, 0.062, "liquid")


# ---- movers -----------------------------------------------------------------------------


def test_movers_are_liquid_or_tradable_beyond_two_sigma_biggest_first() -> None:
    state = pl.DataFrame(
        {
            "isin": ["A", "B", "C", "D", "E"],
            "trade_date": [TUE] * 4 + [date(2026, 11, 16)],
            "surprise": [2.5, -4.0, 1.9, 6.0, 5.0],
            "resid": [0.03, -0.05, 0.02, 0.09, 0.07],
            "liquidity_tier": ["liquid", "tradable", "liquid", "thin", "liquid"],
        }
    )
    assert [m.isin for m in nt.movers(state, TUE)] == ["B", "A"]


# ---- timeline ---------------------------------------------------------------------------


def test_timeline_lists_filings_and_headlines_since_the_previous_close_newest_first() -> None:
    items = [
        _item("Alfa Testowa podpisała umowę za 120 mln zł", _warsaw(11, 17, 10, 5)),
        _item("Alfa Testowa: wywiad z prezesem", _warsaw(11, 17, 12, 0)),  # kept: no event test
        _item("Alfa Testowa miała zysk w III kw.", _warsaw(11, 16, 15, 0)),  # before the window
        _item("Alfa Testowa obniża prognozy", _warsaw(11, 17, 18, 30)),  # after the close
    ]
    reps = _reports(
        [
            {
                "report_id": "r1",
                "title": "Zawarcie znaczącej umowy",
                "signal_type": "inside_info",
                "subtype": "large_contract",
                "known_at": _warsaw(11, 17, 9, 40),
            },
            {
                "report_id": "r2",
                "title": "Informacja o stanie stosowania Dobrych Praktyk",
                "known_at": _warsaw(11, 17, 11, 0),
            },
        ]
    )
    tl = nt.timeline(MOVER, _info(), items, reps, _names(), TUE)
    assert [e["title"][:22] for e in tl] == [
        "Alfa Testowa obniża pr",
        "Alfa Testowa: wywiad z",
        "Informacja o stanie st",
        "Alfa Testowa podpisała",
        "Zawarcie znaczącej umo",
    ]
    assert tl[0]["after_close"] and not tl[1]["after_close"]
    assert [e["routine"] for e in tl if e["kind"] == "filing"] == [True, False]
    events = {e["title"][:22]: e["event"] for e in tl}
    assert events["Alfa Testowa podpisała"] and events["Zawarcie znaczącej umo"]
    assert not events["Alfa Testowa: wywiad z"] and not events["Informacja o stanie st"]


def test_timeline_drops_other_companies_noise_and_repeats() -> None:
    items = [
        _item("Alfa Testowa podpisała umowę z PKP", _warsaw(11, 17, 10, 5)),
        _item("Alfa Testowa podpisała umowę z PKP", _warsaw(11, 17, 10, 7), news.BIZNESRADAR),
        _item("Beta Testowa i Gamma: nowe umowy", _warsaw(11, 17, 11, 0)),  # not about Alfa
        _item("Alfa Testowa ZAKSA - kto wygrał mecz", _warsaw(11, 17, 12, 0)),  # sport
    ]
    tl = nt.timeline(MOVER, _info(), items, _reports([]), _names(), TUE)
    assert [(e["title"], e["source"]) for e in tl] == [
        ("Alfa Testowa podpisała umowę z PKP", "Bankier")
    ]


def test_an_outlets_retitled_headline_is_one_story() -> None:
    # as on 24 Nov 2026: Parkiet re-titled its story about the guarantee within half an hour
    titles = [
        ("Alfa Testowa poręczy kredyt. Gwarancja do 550 mln dol.", _warsaw(11, 17, 14, 43)),
        ("Alfa Testowa poręczy kredyt. Gwarancja do 550 mln USD", _warsaw(11, 17, 15, 13)),
        ("Alfa Testowa: wyniki bez dużych niespodzianek. Kurs się waha", _warsaw(11, 17, 9, 46)),
        ("Alfa Testowa: wyniki bez dużych niespodzianek. Kurs w górę", _warsaw(11, 17, 11, 30)),
    ]
    items = [_item(t, at, outlet="Parkiet") for t, at in titles]
    tl = nt.timeline(MOVER, _info(), items, _reports([]), _names(), TUE)
    assert [(e["title"], e["at"]) for e in tl] == [titles[0], titles[2]]


def test_a_later_version_that_adds_to_a_headline_replaces_it() -> None:
    items = [
        _item("Akcje Alfy Testowej ostro w górę", _warsaw(11, 17, 10, 0), outlet="Parkiet"),
        _item(
            "Kurs Alfy Testowej ostro w górę. Inwestor chce kupić akcje",
            _warsaw(11, 17, 10, 40),
            outlet="Parkiet",
        ),
    ]
    info = common.CompanyInfo(ALFA, "ALFA", "Alfa Testowa", ("Alfy Testowej",))
    tl = nt.timeline(MOVER, info, items, _reports([]), _names(), TUE)
    assert [(e["title"], e["at"]) for e in tl] == [
        ("Kurs Alfy Testowej ostro w górę. Inwestor chce kupić akcje", _warsaw(11, 17, 10, 40))
    ]


def test_two_stories_of_one_outlet_both_stay() -> None:
    items = [
        _item(
            "Alfa Testowa podtrzymuje, że sklepy osiągną rentowność w IV kw.",
            _warsaw(11, 17, 13, 24),
        ),
        _item("Alfa Testowa ocenia, że początek IV kw. jest bardzo dobry", _warsaw(11, 17, 12, 42)),
        _item("Alfa Testowa", _warsaw(11, 17, 14, 0), outlet="Parkiet"),  # too short to compare
    ]
    tl = nt.timeline(MOVER, _info(), items, _reports([]), _names(), TUE)
    assert len(tl) == 3


def test_google_keeps_only_headlines_that_name_an_event() -> None:
    def google(title: str, hh: int) -> NewsItem:
        return _item(title, _warsaw(11, 17, hh, 0), news.GOOGLE, outlet=f"outlet{hh}.test")

    items = [
        # a namesake and a sponsorship: what Google finds for a stock the portals skipped
        google("Rywal Alfa Testowa otwiera pierwszy sklep na Mazowszu", 9),
        google("Tenisistka z nowym sponsorem: Alfa Testowa", 10),
        google("Alfa Testowa had PLN 49 mln in net profit in Q3", 11),  # English, an event
        google("Alfa Testowa ma nowy kontrakt", 12),
        # the portals' own pages are about the company: kept without an event
        _item("Alfa Testowa otwiera drugi sklep na Mazowszu", _warsaw(11, 17, 13, 0)),
    ]
    tl = nt.timeline(MOVER, _info(), items, _reports([]), _names(), TUE)
    assert [(e["title"], e["event"]) for e in tl] == [
        ("Alfa Testowa otwiera drugi sklep na Mazowszu", False),
        ("Alfa Testowa ma nowy kontrakt", True),
        ("Alfa Testowa had PLN 49 mln in net profit in Q3", True),
    ]


def test_date_only_headline_belongs_to_its_day() -> None:
    items = [
        _item("Alfa Testowa ma nowy kontrakt", _warsaw(11, 17, 9, 0), news.GOOGLE, "day"),
        _item("Alfa Testowa ma stary kontrakt", _warsaw(11, 16, 9, 0), news.GOOGLE, "day"),
    ]
    tl = nt.timeline(MOVER, _info(), items, _reports([]), _names(), TUE)
    assert [e["title"] for e in tl] == ["Alfa Testowa ma nowy kontrakt"]
    assert nt.local_time(tl[0]["at"], "day")[0] == ""


def test_a_full_timeline_keeps_the_session_and_drops_the_evening_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nt, "MAX_ITEMS", 4)
    items = [
        _item("Alfa Testowa podpisała umowę", _warsaw(11, 17, 9, 30)),
        _item("Alfa Testowa: wywiad z prezesem", _warsaw(11, 17, 11, 0)),
        _item("Alfa Testowa na konferencji", _warsaw(11, 17, 15, 0)),  # latest of the session
        _item("Alfa Testowa obniża prognozy", _warsaw(11, 17, 18, 30)),  # after the close
        _item("Alfa Testowa: komentarz analityka", _warsaw(11, 17, 20, 0)),  # after the close
    ]
    reps = _reports(
        [
            {
                "report_id": "r1",
                "title": "Zawarcie znaczącej umowy",
                "signal_type": "inside_info",
                "known_at": _warsaw(11, 17, 9, 10),
            },
            {
                "report_id": "r2",
                "title": "Informacja o stanie stosowania Dobrych Praktyk",
                "known_at": _warsaw(11, 17, 9, 0),
            },
        ]
    )
    tl = nt.timeline(MOVER, _info(), items, reps, _names(), TUE)
    assert [e["title"][:22] for e in tl] == [  # still newest first
        "Alfa Testowa na konfer",
        "Alfa Testowa: wywiad z",
        "Alfa Testowa podpisała",
        "Zawarcie znaczącej umo",
    ]


# ---- collection -------------------------------------------------------------------------


class _Client:
    def close(self) -> None:
        pass


def test_a_source_that_keeps_failing_is_left_out_and_the_rest_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = [nt.Mover(f"PLTEST00000{i}", 3.0, 0.05, "liquid") for i in range(1, 6)]
    infos = {m.isin: common.CompanyInfo(m.isin, f"T{i}", f"Firma {i}") for i, m in enumerate(moved)}
    calls: list[str] = []

    def page(client: Any, source: str, info: Any, *a: Any) -> news.PageResult:
        calls.append(source)
        if source == news.BIZNESRADAR:
            return news.PageResult(Outcome.UNREACHABLE, [], None, "connect timeout")
        return news.PageResult(Outcome.EMPTY, [])

    def google(client: Any, co: Any, **kw: Any) -> news.PageResult:
        calls.append(news.GOOGLE)
        return news.PageResult(Outcome.EMPTY, [])

    monkeypatch.setattr(nt, "_page_items", page)
    monkeypatch.setattr(news, "fetch_google", google)
    res = nt.collect(
        tmp_path,
        TUE,
        moved,
        infos,
        common.NameIndex({}),
        make_client=lambda s: _Client(),  # type: ignore[arg-type,return-value]
        now=lambda: NOW,
    )
    assert calls.count(news.BIZNESRADAR) == nt.DOWN_AFTER
    assert calls.count(news.BANKIER) == 5 and calls.count(news.GOOGLE) == 5
    assert set(res.down) == {news.BIZNESRADAR}


def _symbol_row(symbol: str | None, outcome: str, checked_at: datetime) -> dict[str, Any]:
    return {
        "isin": ALFA,
        "source": news.BANKIER,
        "symbol": symbol,
        "page_name": "Alfa Testowa SA" if symbol else None,
        "outcome": outcome,
        "checked_at": checked_at,
    }


def _portal(
    monkeypatch: pytest.MonkeyPatch,
    found: tuple[Outcome, str | None],
    pages: list[news.PageResult],
) -> tuple[list[str], list[str]]:
    """Fake symbol lookup and page fetch; returns the guesses looked up and symbols fetched."""
    looked: list[str] = []
    fetched: list[str] = []

    def resolve(client: Any, source: str, company: Any, guess: str, **kw: Any) -> Any:
        looked.append(guess)
        outcome, symbol = found
        sym = news.PortalSymbol(source, symbol, "Alfa Testowa SA") if symbol else None
        return outcome, sym, str(outcome)

    def fetch(client: Any, source: str, company: Any, **kw: Any) -> news.PageResult:
        fetched.append(kw["symbol"])
        return pages.pop(0)

    monkeypatch.setattr(news, "resolve_symbol", resolve)
    monkeypatch.setattr(news, "fetch_company", fetch)
    return looked, fetched


def _page(symbols: Any, at: datetime) -> news.PageResult:
    return nt._page_items(
        _Client(),  # type: ignore[arg-type]
        news.BANKIER,
        _info(),
        symbols,
        at,
        at,
        lambda: at,
    )


def test_a_failed_symbol_lookup_is_tried_again_after_a_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked, fetched = _portal(monkeypatch, (Outcome.OK, "ALF"), [news.PageResult(Outcome.OK, [])])
    symbols = nt._Symbols(tmp_path)
    symbols.set(_symbol_row(None, "http_error", NOW))  # a server error on the first night

    assert _page(symbols, NOW + timedelta(days=1)).detail == "no symbol"
    assert looked == [] and fetched == []

    assert _page(symbols, NOW + timedelta(days=nt.RECHECK_DAYS)).outcome is Outcome.OK
    assert looked == ["ALFA"] and fetched == ["ALF"]
    assert symbols.get(ALFA, news.BANKIER)["outcome"] == "ok"  # type: ignore[index]


def test_a_symbol_whose_page_shows_another_company_is_looked_up_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        news.PageResult(Outcome.EMPTY, [], None, news.NOT_THIS_COMPANY),
        news.PageResult(Outcome.OK, []),
    ]
    looked, fetched = _portal(monkeypatch, (Outcome.OK, "NEW"), pages)
    symbols = nt._Symbols(tmp_path)
    symbols.set(_symbol_row("OLD", "ok", NOW))

    assert _page(symbols, NOW).outcome is Outcome.OK
    assert fetched == ["OLD", "NEW"] and looked == ["ALFA"]
    assert symbols.get(ALFA, news.BANKIER)["symbol"] == "NEW"  # type: ignore[index]


def test_a_failed_lookup_keeps_the_symbol_that_worked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [news.PageResult(Outcome.HTTP_ERROR, [], None, "HTTP 500")]
    looked, fetched = _portal(monkeypatch, (Outcome.HTTP_ERROR, None), pages)
    symbols = nt._Symbols(tmp_path)
    symbols.set(_symbol_row("ALF", "ok", NOW))

    assert _page(symbols, NOW).outcome is Outcome.HTTP_ERROR
    assert fetched == ["ALF"] and looked == ["ALFA"]
    assert symbols.get(ALFA, news.BANKIER)["symbol"] == "ALF"  # type: ignore[index]


def test_collected_items_land_in_bronze_by_publication_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    it = _item("Alfa Testowa podpisała umowę", _warsaw(11, 17, 10, 5))

    def page(client: Any, source: str, info: Any, *a: Any) -> news.PageResult:
        return news.PageResult(Outcome.OK, [it] if source == news.BANKIER else [])

    monkeypatch.setattr(nt, "_page_items", page)
    monkeypatch.setattr(news, "fetch_google", lambda *a, **k: pytest.fail("pages had news"))
    nt.collect(
        tmp_path,
        TUE,
        [MOVER],
        {ALFA: _info()},
        _names(),
        make_client=lambda s: _Client(),  # type: ignore[arg-type,return-value]
        now=lambda: NOW,
    )
    df = lake.read_partition(tmp_path, "bronze", nt.TABLE, TUE)
    assert df is not None and df["title"].to_list() == [it.title]


# ---- digest -----------------------------------------------------------------------------


def _entry(kind: str, at: datetime, title: str, **kw: Any) -> dict[str, Any]:
    base = {"kind": kind, "at": at, "time": f"{at.astimezone(news.WARSAW):%H:%M}"}
    return {**base, "title": title, "source": "ESPI" if kind == "filing" else "Bankier", **kw}


def test_digest_line_prefers_the_sessions_first_filing() -> None:
    tl = [
        _entry("headline", _warsaw(11, 17, 8, 0), "Alfa Testowa ma umowę z PKP", event=True),
        _entry("filing", _warsaw(11, 17, 18, 30), "Zawarcie aneksu", event=True, after_close=True),
        _entry("filing", _warsaw(11, 17, 11, 0), "Zawarcie znaczącej umowy", event=True),
        _entry("filing", _warsaw(11, 17, 9, 40), "Dobre Praktyki", routine=True),
        _entry("filing", _warsaw(11, 17, 14, 0), "Szacunkowe wyniki za III kw.", event=True),
    ]
    assert publish.mover_line(tl) == "First news: ESPI 11:00 · Zawarcie znaczącej umowy (+3 more)"


def test_digest_line_without_a_filing_takes_the_first_headline_naming_an_event() -> None:
    tl = [
        _entry("headline", _warsaw(11, 17, 8, 0), "Alfa Testowa: wywiad z prezesem"),
        _entry("headline", _warsaw(11, 17, 12, 0), "Alfa Testowa obniża prognozy", event=True),
        _entry("headline", _warsaw(11, 17, 10, 0), "Alfa Testowa ma umowę z PKP", event=True),
        _entry("headline", _warsaw(11, 17, 9, 0), "Alfa Testowa zysk", event=True, precision="day"),
    ]
    assert (
        publish.mover_line(tl)
        == "First news: Bankier 10:00 · Alfa Testowa ma umowę z PKP (+3 more)"
    )


def test_digest_line_counts_what_names_no_event() -> None:
    heads = [_entry("headline", _warsaw(11, 17, h, 0), f"Alfa {h}") for h in (10, 12)]
    late = _entry("headline", _warsaw(11, 17, 19, 0), "Alfa obniża prognozy", event=True)
    late["after_close"] = True
    assert publish.mover_line([*heads, late]) == (
        "3 items published, none naming an event before the close."
    )
    routine = [_entry("filing", _warsaw(11, 17, 9, 0), "Dobre Praktyki", routine=True)]
    assert publish.mover_line(routine) == "Nothing published since the previous close."


def test_digest_lists_other_movers_with_their_first_news() -> None:
    ctx = publish.SiteContext(
        day=TUE,
        generated_at=NOW,
        run={},
        counts={"reports": 3},
        worth=[],
        feed=[],
        stats={},
        summary={},
        events=[],
        runs=[],
        coverage={},
        health="Run ok",
        site_url="https://example.test/",
        movers=[
            {
                "isin": ALFA,
                "ticker": "ALFA",
                "z": 3.4,
                "move": 0.062,
                "timeline": [
                    _entry("headline", _warsaw(11, 17, 10, 0), "Alfa podpisała umowę", event=True)
                ],
            },
            {"isin": BETA, "ticker": "BETA", "z": -2.5, "move": -0.03, "timeline": []},
        ],
    )
    text, markup = publish.digest_message(ctx)
    assert "<b>ALFA</b> +6.2% vs market (+3.4σ)" in text
    assert "First news: Bankier 10:00 · Alfa podpisała umowę" in text
    assert "(not a stated cause)" in text
    assert "Nothing published since the previous close." in text
    assert markup["inline_keyboard"][0][-1]["url"].endswith("reports.html#bigmoves-h")


def test_timeline_ends_at_the_next_sessions_open() -> None:
    items = [
        _item("Alfa Testowa obniża prognozy", _warsaw(11, 17, 18, 30)),  # evening: kept
        _item("Alfa Testowa podpisała umowę", _warsaw(11, 18, 9, 30)),  # next session: dropped
    ]
    reps = _reports(
        [
            {
                "report_id": "r9",
                "title": "Raport kwartalny",
                "kind": "Kwartalny",
                "signal_type": "periodic",
                "known_at": _warsaw(11, 19, 16, 0),
            }
        ]
    )
    tl = nt.timeline(MOVER, _info(), items, reps, _names(), TUE)
    assert [e["title"] for e in tl] == ["Alfa Testowa obniża prognozy"]


def test_round_up_columns_are_wraps() -> None:
    it = _item("Allegro, Alfa Testowa i NTT w centrum uwagi", _warsaw(11, 17, 9, 0))
    assert news.verdict(it, _info().company(), require_event=False).reason == "excluded:market_wrap"
