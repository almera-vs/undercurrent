"""The big movers' news timeline (no network)."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
    title: str, at: datetime, source: str = news.BANKIER, prec: Literal["minute", "day"] = "minute"
) -> NewsItem:
    url = f"https://x.test/{abs(hash((title, at)))}"
    return NewsItem(ALFA, source, "Bankier", at, prec, title, url, NOW)


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


def test_digest_line_names_the_sessions_first_filing_never_a_headline() -> None:
    tl = [
        _entry("headline", _warsaw(11, 17, 18, 0), "Alfa Testowa: analitycy tną prognozy"),
        _entry("filing", _warsaw(11, 17, 18, 30), "Rezygnacja członka zarządu", after_close=True),
        _entry("filing", _warsaw(11, 17, 11, 0), "Zawarcie znaczącej umowy"),
        _entry("filing", _warsaw(11, 17, 9, 40), "Dobre Praktyki", routine=True),
        _entry("filing", _warsaw(11, 17, 14, 0), "Szacunkowe wyniki za III kw."),
    ]
    assert publish.mover_line(tl) == "ESPI 11:00: Zawarcie znaczącej umowy"
    heads = [_entry("headline", _warsaw(11, 17, h, 0), f"Alfa {h}") for h in (10, 12)]
    assert publish.mover_line(heads) == "2 items published since the previous close, no filing."
    routine = [_entry("filing", _warsaw(11, 17, 9, 0), "Dobre Praktyki", routine=True)]
    assert publish.mover_line(routine) == "Nothing published since the previous close."


def test_digest_lists_other_movers_without_setting_a_headline_beside_the_move() -> None:
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
                "timeline": [_entry("headline", _warsaw(11, 17, 10, 0), "Alfa podpisała umowę")],
            },
            {"isin": BETA, "ticker": "BETA", "z": -2.5, "move": -0.03, "timeline": []},
        ],
    )
    text, markup = publish.digest_message(ctx)
    assert "<b>ALFA</b> +6.2% vs market (+3.4σ)" in text
    assert "1 item published since the previous close, no filing." in text
    assert "Alfa podpisała umowę" not in text
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
