"""Corporate calendar: dividends, meetings, report dates and the check against the exchange."""

import json
from datetime import date

import polars as pl
import pytest

from undercurrent import corp_calendar as cc


def test_ex_date_is_the_session_before_the_record_date():
    assert cc.ex_date(date(2026, 6, 3)) == date(2026, 6, 2)  # Wednesday → Tuesday
    assert cc.ex_date(date(2026, 6, 1)) == date(2026, 5, 29)  # Monday → Friday
    # a record date on a Saturday counts from Friday, the last session before it
    assert cc.ex_date(date(2015, 7, 4)) == date(2015, 7, 2)
    # Easter Monday 2026 is closed: the session before Tuesday 7 April is Thursday 2 April
    # (Good Friday is closed too)
    assert cc.ex_date(date(2026, 4, 7)) == date(2026, 4, 2)


def test_exchange_form_with_two_instalments_sums_them():
    body = (
        "1 2 Dzień dywidendy 2026-06-16 2026-06-16 Dzień wypłaty transzy 2026-06-26 2026-09-14 "
        "Wysokość transzy 3 530 400 2 647 800 Wartość dywidendy przypadająca na akcję 0,08 0,06 "
        "Pole tekstowe Zgodnie z uchwałą Zwyczajnego Walnego Zgromadzenia …"
    )
    d = cc.extract_dividend(
        "Decyzja o wypłacie dywidendy oraz informacje dotyczące dywidendy", body
    )
    assert d is not None and d.stage == "resolved"
    assert d.dps == pytest.approx(0.14)
    assert d.record_date == date(2026, 6, 16) and d.pay_date == date(2026, 6, 26)


@pytest.mark.parametrize(
    ("text", "dps"),
    [
        ("kwota dywidendy przypadająca na jedną akcję wynosi 7 złotych 43 grosze.", 7.43),
        ("na każdą akcję przysługuje dywidenda w kwocie 9 groszy.", 0.09),
        ("tj. 0,50 zł _słownie: pięćdziesiąt groszy_ na jedną akcję Spółki", 0.50),
        ("w wysokości 0,55 zł 55/100 złotych na jedną akcję.", 0.55),
        ("tj. 33, 00 zł na jedną akcję.", 33.0),
    ],
)
def test_amount_per_share_in_its_many_spellings(text, dps):
    body = f"Walne zgromadzenie podjęło uchwałę o wypłacie dywidendy, {text}"
    d = cc.extract_dividend("Wypłata dywidendy", body, date(2026, 5, 1))
    assert d is not None and d.dps == pytest.approx(dps)


@pytest.mark.parametrize(
    "phrase",
    [
        'w dniu 31 lipca 2026 roku _"dzień dywidendy"_, '
        "a termin wypłaty dywidendy na 31 sierpnia 2026",
        "ustaliło datę 31 lipca 2026 r. jako dzień dywidendy oraz datę 31 sierpnia 2026 r. "
        "jako termin wypłaty dywidendy",
        "wyznaczyło: 31 lipca 2026 roku jako dzień, według którego ustala się listę "
        "akcjonariuszy uprawnionych do dywidendy, 31 sierpnia 2026 roku jako termin wypłaty "
        "dywidendy",
        "dniem ustalenia prawa do dywidendy będzie 31 lipca 2026 r., a dniem wypłaty "
        "dywidendy 31 sierpnia 2026 r.",
    ],
)
def test_record_and_payment_dates_whichever_way_they_are_written(phrase):
    body = f"Zwyczajne walne zgromadzenie podjęło uchwałę: 1,00 zł na jedną akcję; {phrase}."
    d = cc.extract_dividend("Uchwała o wypłacie dywidendy", body, date(2026, 6, 20))
    assert d is not None
    assert (d.record_date, d.pay_date) == (date(2026, 7, 31), date(2026, 8, 31))


def test_a_fiscal_year_date_is_not_a_record_date():
    body = (
        "uchwała w sprawie ustalenia dnia dywidendy i dnia wypłaty dywidendy, zgodnie z którą zysk "
        "za okres od 1 stycznia 2025 roku do 31 grudnia 2025 roku przeznaczono na wypłatę "
        "dywidendy, 0,10 zł na jedną akcję. Dzień dywidendy ustalono na 30 czerwca 2026 roku."
    )
    d = cc.extract_dividend("Uchwała ZWZ", body, date(2026, 5, 31))
    assert d is not None and d.record_date == date(2026, 6, 30)


def test_a_subsidiarys_dividend_is_not_the_issuers():
    body = "Walne zgromadzenie spółki zależnej od Emitenta podjęło uchwałę o wypłacie dywidendy …"
    assert cc.extract_dividend("Uchwała o wypłacie dywidendy przez spółkę zależną", body) is None


def test_stage_follows_the_wording():
    rec = cc.extract_dividend(
        "Rekomendacja Zarządu dotycząca wypłaty dywidendy",
        "Zarząd rekomenduje wypłatę dywidendy w wysokości 2,00 zł na jedną akcję.",
    )
    adv = cc.extract_dividend(
        "Zaliczka na poczet dywidendy",
        "Zarząd postanowił wypłacić zaliczkę na poczet dywidendy, 1,00 zł na jedną akcję.",
    )
    assert rec is not None and rec.stage == "proposed"
    assert adv is not None and adv.stage == "interim"


def _divs(rows):
    return pl.DataFrame(rows, schema=cc.DIVIDEND_SCHEMA)


def test_the_resolution_replaces_the_recommendation_and_keeps_its_fields():
    base = {"isin": "PL0000000001", "currency": "PLN", "fiscal_year": 2025, "notes": None}
    rows = [
        base
        | {
            "report_id": "r1",
            "published": date(2026, 3, 1),
            "stage": "proposed",
            "dps": 2.0,
            "record_date": date(2026, 6, 10),
            "ex_date": date(2026, 6, 9),
            "pay_date": date(2026, 6, 20),
        },
        base
        | {
            "report_id": "r2",
            "published": date(2026, 5, 20),
            "stage": "resolved",
            "dps": 2.5,
            "record_date": date(2026, 6, 10),
            "ex_date": date(2026, 6, 9),
            "pay_date": None,
        },
        # next year's dividend is another one
        base
        | {
            "report_id": "r3",
            "published": date(2027, 3, 1),
            "stage": "proposed",
            "dps": 3.0,
            "record_date": None,
            "ex_date": None,
            "pay_date": None,
            "fiscal_year": 2026,
        },
    ]
    out = cc.latest_dividends(_divs(rows)).sort("published")
    assert out.height == 2
    first = out.row(0, named=True)
    assert (first["report_id"], first["stage"], first["dps"]) == ("r2", "resolved", 2.5)
    assert first["pay_date"] == date(2026, 6, 20)  # the resolution left it out


def test_check_against_exchange_allows_a_tick():
    divs = _divs(
        [
            {
                "report_id": "a",
                "isin": "X1",
                "published": date(2026, 5, 1),
                "stage": "resolved",
                "dps": 2.60,
                "currency": "PLN",
                "record_date": date(2026, 6, 3),
                "ex_date": date(2026, 6, 2),
                "pay_date": None,
                "fiscal_year": None,
                "notes": None,
            },
            {
                "report_id": "b",
                "isin": "X2",
                "published": date(2026, 5, 1),
                "stage": "resolved",
                "dps": 0.0065,
                "currency": "PLN",
                "record_date": date(2026, 6, 3),
                "ex_date": date(2026, 6, 2),
                "pay_date": None,
                "fiscal_year": None,
                "notes": None,
            },
        ]
    )
    prices = pl.DataFrame(
        {
            "isin": ["X1", "X2"],
            "trade_date": [date(2026, 6, 2)] * 2,
            "close": [118.0, 12.0],
            "prev_close": [120.0, 12.65],
            # the exchange rounded 2.60 to a tick of the 120 zł price; 0.65 is not 0.0065
            "exch_reference": [117.5, 12.0],
        }
    )
    got = dict(cc.check_against_exchange(divs, prices).select("report_id", "agrees").iter_rows())
    assert got == {"a": True, "b": False}


def test_meetings_convened_and_cancelled():
    m = cc.extract_meeting(
        "Zwołanie Nadzwyczajnego Walnego Zgromadzenia na dzień 5 marca 2026 r.",
        "",
        date(2026, 2, 1),
    )
    assert m == cc.Meeting("nwz", "convened", date(2026, 3, 5))
    m = cc.extract_meeting(
        "Ogłoszenie o zwołaniu Zwyczajnego Walnego Zgromadzenia na dzień 28 czerwca 2017r.",
        "",
        date(2017, 6, 1),
    )
    assert m is not None and m.meeting_date == date(2017, 6, 28)
    m = cc.extract_meeting(
        "Odwołanie Zwyczajnego Walnego Zgromadzenia zwołanego na dzień 12 stycznia 2026 r.",
        "",
        date(2026, 1, 5),
    )
    assert m is not None and m.status == "cancelled"
    assert cc.extract_meeting("Przerwa w obradach Walnego Zgromadzenia", "", None) is None


def test_report_schedule_skips_the_dates_that_end_a_period():
    body = (
        "Zarząd podaje harmonogram publikowania raportów okresowych w 2026 roku: "
        "- raport kwartalny za IV kwartał 2025 r. – 13 lutego 2026 r. "
        "- raport roczny za rok obrotowy zakończony 31 grudnia 2025 r. – 20 marca 2026 r. "
        "- raport kwartalny za I kwartał 2026 r. – w dniu 15/05/2026 r. "
        "- raport półroczny za I półrocze 2026 r. – 10 września 2026 r."
    )
    got = cc.extract_report_dates(
        "Harmonogram publikacji raportów okresowych", body, date(2026, 1, 10)
    )
    assert sorted(got, key=lambda x: x[2]) == [
        ("q4", 2025, date(2026, 2, 13)),
        ("annual", 2025, date(2026, 3, 20)),
        ("q1", 2026, date(2026, 5, 15)),
        ("h1", 2026, date(2026, 9, 10)),
    ]


def test_drop_off_ratio_takes_out_the_market():
    rows = []
    for i in range(30):
        # each price falls by 81 % of its dividend on a day WIG is flat
        rows.append(
            {
                "ex_date": date(2026, 6, 1 + i % 20),
                "prev_close": 100.0,
                "close": 100.0 - 0.81 * 4.0,
                "dps": 4.0,
                "agrees": True,
                "yield_": 0.04,
            }
        )
    checked = pl.DataFrame(rows)
    bench = {d: 0.0 for d in checked["ex_date"]}
    got = cc.drop_off(checked, bench)
    assert got["n"] == 30 and got["median"] == pytest.approx(0.81)


def test_context_lists_what_is_ahead_and_skips_what_came_out(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    day = date(2026, 11, 12)
    stock = {"isin": "PL0000000001", "ticker": "TST", "slug": "tst", "name": "Test S.A."}
    _divs(
        [
            {
                "report_id": "d",
                "isin": stock["isin"],
                "published": date(2026, 10, 1),
                "stage": "interim",
                "dps": 2.0,
                "currency": "PLN",
                "record_date": date(2026, 12, 2),
                "ex_date": date(2026, 12, 1),
                "pay_date": date(2026, 12, 9),
                "fiscal_year": 2026,
                "notes": None,
            }
        ]
    ).write_parquet(gold / cc.GOLD["dividends"])
    pl.DataFrame(
        [
            {
                "report_id": "m",
                "isin": stock["isin"],
                "published": date(2026, 11, 1),
                "kind": "nwz",
                "status": "convened",
                "meeting_date": date(2026, 11, 30),
            }
        ],
        schema=cc.MEETING_SCHEMA,
    ).write_parquet(gold / cc.GOLD["meetings"])
    sched = pl.DataFrame(
        [
            {
                "report_id": "s",
                "isin": stock["isin"],
                "published": date(2026, 1, 5),
                "period": "q3",
                "fiscal_year": 2026,
                "report_date": date(2026, 11, 20),
            },
            {
                "report_id": "s",
                "isin": stock["isin"],
                "published": date(2026, 1, 5),
                "period": "annual",
                "fiscal_year": 2025,
                "report_date": date(2026, 3, 20),
            },
        ],
        schema=cc.REPORT_DATE_SCHEMA,
    ).with_columns(done=pl.Series([True, False]))
    sched.write_parquet(gold / cc.GOLD["report_dates"])
    (gold / cc.STATS_FILE).write_text(json.dumps({"checked": 0, "drop": {"n": 0}, "dates": {}}))
    ctx = cc.context(tmp_path, day, {stock["isin"]: stock}, {stock["isin"]: 50.0})
    kinds = [(e["date"], e["kind"]) for e in ctx["by_isin"][stock["isin"]]]
    # the Q3 report already came out early; the March report is in the past
    assert kinds == [
        (date(2026, 11, 30), "nwz"),
        (date(2026, 12, 1), "ex"),
        (date(2026, 12, 9), "pay"),
    ]
    assert ctx["dividends"][0]["yield"] == pytest.approx(0.04)
    assert cc.context(tmp_path / "nothing", day, {}, {}) is None
