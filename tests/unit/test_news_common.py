"""What the timeline and the coverage study share: names, sessions, report ranks.

Fictional companies (Alfa Testowa, Beta Testowa); dates are real GPW sessions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from undercurrent import news
from undercurrent import news_common as common
from undercurrent.news import NewsItem
from undercurrent.news_common import report_rank

ALFA = "PLTEST000001"
BETA = "PLTEST000002"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
# Tuesday 17 Nov 2026 is a session; Monday 16 Nov is its previous session.
TUE = date(2026, 11, 17)
MON = date(2026, 11, 16)


def _warsaw(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=news.WARSAW).astimezone(UTC)


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("GRUPA AZOTY SPÓŁKA AKCYJNA", "GRUPA AZOTY"),
        ("Grupa Azoty S.A.", "Grupa Azoty"),
        ("XTB SA", "XTB"),
        ("Alfa Testowa ASI S.A.", "Alfa Testowa"),
        (None, ""),
    ],
)
def test_plain_name_drops_legal_form(raw: str | None, plain: str) -> None:
    assert common.plain_name(raw) == plain


def test_session_before_close_is_that_day() -> None:
    assert common.session_of(_warsaw(2026, 11, 17, 17, 4)) == TUE


def test_session_after_close_is_the_next_session() -> None:
    # after Tuesday's close: Wednesday
    assert common.session_of(_warsaw(2026, 11, 17, 17, 5)) == date(2026, 11, 18)


def test_weekend_news_belongs_to_monday() -> None:
    assert common.session_of(_warsaw(2026, 11, 14, 11, 0)) == MON  # Saturday
    assert common.session_of(_warsaw(2026, 11, 13, 19, 0)) == MON  # Friday after the close


def test_date_only_item_belongs_to_its_date_even_late_in_utc() -> None:
    # a date-only placeholder never moves to the next session because of its fake time
    assert common.session_of(_warsaw(2026, 11, 17, 23, 0), "day") == TUE
    assert common.session_of(_warsaw(2026, 11, 15, 9, 0), "day") == MON  # Sunday -> Monday


def test_name_index_counts_other_companies() -> None:
    cos = {
        ALFA: news.Company(ALFA, ("Alfa Testowa",), "ALFA"),
        BETA: news.Company(BETA, ("Beta Testowa",), "BETA"),
    }
    idx = common.NameIndex(cos)
    assert idx.others("Alfa Testowa i Beta Testowa w górę", ALFA) == ["Beta Testowa"]
    assert idx.others("Alfa Testowa w górę", ALFA) == []


def test_own_hosts_from_google_outlet_name() -> None:
    info = common.CompanyInfo(ALFA, "ALFA", "Alfa Testowa")
    own = NewsItem(
        ALFA,
        news.GOOGLE,
        "Alfa Testowa",
        NOW,
        "minute",
        "Oferta",
        "https://news.google.com/x",
        NOW,
        outlet_url="https://www.alfatestowa.example",
    )
    other = NewsItem(
        ALFA,
        news.GOOGLE,
        "Parkiet",
        NOW,
        "minute",
        "Alfa Testowa",
        "https://news.google.com/y",
        NOW,
        outlet_url="https://www.parkiet.com",
    )
    assert common.own_hosts([own, other], info) == {"alfatestowa.example"}


def test_name_index_short_tickers_only_in_upper_case() -> None:
    cos = {
        ALFA: news.Company(ALFA, ("Alfa Testowa",), "ALFA"),
        BETA: news.Company(BETA, ("Beta Testowa",), "BTA"),
    }
    idx = common.NameIndex(cos)
    assert idx.others("Alfa Testowa, BTA i inni", ALFA) == ["BTA"]
    assert idx.others("Alfa Testowa bta", ALFA) == []


def test_report_rank_keeps_reports_that_can_move_a_price() -> None:
    assert report_rank("Kwartalny", "periodic", None, "") == 0
    assert report_rank("Bieżący", "inside_info", "profit_warning", "Wstępne wyniki") == 0
    assert report_rank("Bieżący", "inside_info", "financing", "Aneks do umowy kredytu") == 1
    assert (
        report_rank("Bieżący", "other", None, "Zaproszenie do składania ofert sprzedaży akcji") == 2
    )
    assert report_rank("Bieżący", "other", None, "", '[{"name": "abc-2025-12-31-pl.xbri"}]') == 0
    for title in (
        "Informacja o stanie stosowania Dobrych Praktyk 2021",
        "Wykaz akcjonariuszy posiadających co najmniej 5% głosów na NWZ",
        "Powołanie osoby zarządzającej lub nadzorującej",
    ):
        assert report_rank("Bieżący", "other", None, title) is None, title
    assert (
        report_rank(
            "Bieżący", "insider_tx", None, "Powiadomienie o transakcji w trybie art. 19 MAR"
        )
        is None
    )


def test_exchange_name_is_noise_for_the_exchange_operator() -> None:
    gpw = common.CompanyInfo(
        "PLGPW0000017", "GPW", "Giełda Papierów Wartościowych w Warszawie"
    ).company()
    item = NewsItem(
        gpw.isin,
        news.BANKIER,
        "Bankier",
        NOW,
        "minute",
        "Takich spadków nie było na GPW od miesięcy, wyniki spółek słabe",
        "https://x/1",
        NOW,
    )
    assert news.verdict(item, gpw).reason == "excluded:noise"


def test_routine_notice_with_an_event_word_is_not_a_cause() -> None:
    assert (
        report_rank("Bieżący", "other", None, "Informacja o rejestracji zmian Statutu Spółki")
        is None
    )
    title = "Dopuszczenie do obrotu euroobligacji podporządkowanych Tier 2 serii 7"
    assert report_rank("Bieżący", "other", None, title) is None
