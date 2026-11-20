"""Holders of 5 % or more: notices, meeting lists, matching and the register."""

from datetime import date

import polars as pl
import pytest

from undercurrent import ownership as ow


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Pana Jana Kowalskiego", "Jan Kowalski"),  # declension
        ("Alfa Capital sp. z o.o.", "Alfa Capital Sp. z o. o."),  # legal forms
        ("IDM S.A. w upadłości układowej", "IDM SA"),
        ("Nationale-Nederlanden Otwartego Funduszu Emerytalnego", "NN OFE"),
        ("Powszechne Towarzystwo Emerytalne PZU S.A.", 'OFE PZU "Złota Jesień"'),  # either order
        ("Nationale - Nederlanden", "Nationale-Nederlanden OFE"),
    ],
)
def test_one_holder_whatever_form_the_issuer_used(a, b):
    assert ow.holder_key(a) == ow.holder_key(b)


def test_pension_and_investment_arms_are_different_holders():
    assert ow.holder_key("PZU OFE") != ow.holder_key("TFI PZU S.A.")
    assert ow.holder_key("PKO BP Bankowy OFE") != ow.holder_key("PKO TFI S.A.")


@pytest.mark.parametrize(
    ("body", "name"),
    [
        (
            "Zarząd informuje, że otrzymał od Pana Jana Kowalskiego zaktualizowane zawiadomienie.",
            "Jana Kowalskiego",
        ),
        (
            "Do Spółki wpłynęło zawiadomienie spółki Beta Invest S.A. z dnia 16.04.2018r. …",
            "Beta Invest S.A.",
        ),
        (
            "otrzymał zawiadomienie od Gamma sp. z o.o. z siedzibą w Warszawie o zmianie udziału",
            "Gamma sp. z o.o.",
        ),
        (
            "dotyczące zmiany udziału przez Pana Adama Nowaka oraz Delta Foundation FR.",
            "Adama Nowaka",
        ),
        (
            "W wykonaniu obowiązku Jan Kalino poinformował pismem, że jego udział spadł.",
            "Jan Kalino",
        ),
    ],
)
def test_the_holder_is_named_without_the_clause_after_it(body, name):
    assert ow.holder_name("Zawiadomienie", body) == name


def test_stake_before_and_after_not_the_trade_or_the_threshold():
    body = (
        "zawiadomienie od Alfa FIO o zwiększeniu stanu posiadania akcji Spółki powyżej 5% ogólnej "
        "liczby głosów. Akcjonariusz nabył 64 784 akcje, stanowiące 0,34% ogólnej liczby głosów. "
        "Przed powyższym nabyciem akcji Akcjonariusz posiadał 934 326 akcji, co stanowiło 4,91% "
        "ogólnej liczby głosów. Po powyższych nabyciach Akcjonariusz posiada 999 110 akcji, "
        "stanowiących 5,25% ogólnej liczby głosów."
    )
    h = ow.extract_holding_change("Zawiadomienie od akcjonariusza", body)
    assert (h.pct_before, h.pct_after, h.direction, h.threshold) == (4.91, 5.25, "up", 5.0)


def test_a_share_issue_moves_the_stake_without_a_trade():
    body = (
        "Przed rejestracją akcji serii I Akcjonariusz posiadał 23.182.474 akcje, reprezentujące "
        "31,6 proc. głosów na Walnym Zgromadzeniu. Po rejestracji akcji serii I Akcjonariusz "
        "posiada 23.182.474 akcje, reprezentujące 26,5 proc. głosów na Walnym Zgromadzeniu."
    )
    h = ow.extract_holding_change("Zmiana stanu posiadania", body)
    assert (h.pct_before, h.pct_after, h.direction) == (31.6, 26.5, "down")


def test_attachment_only_notice_keeps_the_stake_empty():
    h = ow.extract_holding_change(
        "Zawiadomienie o zmianie stanu posiadania",
        "Zarząd otrzymał od Omega S.A. zawiadomienie, którego treść stanowi załącznik.",
    )
    assert h.holder == "Omega S.A." and h.pct_after is None
    assert "stake not in the text (attachment only)" in h.notes


@pytest.mark.parametrize(
    "body",
    [
        "Akcjonariusz: Jan Kowalski Liczba akcji: 2 839 940 Udział w liczbie głosów na ZWZ: 49,22% "
        "Udział w ogólnej liczbie głosów: 34,04% Akcjonariusz: Alfa S.A. Liczba akcji: 905 030 "
        "Udział w ogólnej liczbie głosów %: 12,50% Udział w liczbie głosów na ZWZ %: 18,05%",
        "wykaz akcjonariuszy: 1_ Jan Kowalski - liczba głosów: 2 839 940, co uprawniało do 49,22% "
        "głosów na ZWZ oraz stanowi 34,04% ogólnej liczby głosów; 2_ Alfa S.A. - liczba głosów: "
        "905 030, co uprawniało do 18,05% głosów na ZWZ oraz stanowi 12,50% ogólnej liczby głosów.",
        "akcjonariuszami byli: - Jan Kowalski wykonywał prawo głosu z 2 839 940 akcji, 49,22% "
        "głosów obecnych oraz 34,04% ogólnej liczby głosów; - spółka Alfa S.A. z siedzibą w "
        "Krakowie, dysponująca 905 030 akcjami, 18,05% głosów na ZWZ oraz 12,50% ogólnej liczby.",
        "Akcjonariusz Liczba głosów Udział na WZA Udział w ogólnej liczbie głosów Jan Kowalski "
        "2.839.940 49,22% 34,04% Alfa S.A. 905.030 18,05% 12,50%",
    ],
)
def test_meeting_list_formats_and_the_smaller_share_is_of_all_votes(body):
    rows = ow.extract_meeting_list(body)
    got = {(ow.holder_key(r.holder), r.pct_total) for r in rows}
    assert got == {(ow.holder_key("Jan Kowalski"), 34.04), (ow.holder_key("Alfa S.A."), 12.50)}


def _obs(rows):
    return pl.DataFrame(rows, schema=ow.OBS_SCHEMA)


def test_register_keeps_the_last_stake_and_drops_who_fell_below_five():
    base = {
        "source": "notice",
        "pct_before": None,
        "direction": None,
        "threshold": None,
        "notes": None,
    }
    obs = _obs(
        [
            base
            | {
                "report_id": "1",
                "isin": "X",
                "published": date(2025, 1, 1),
                "holder": "Alfa S.A.",
                "holder_key": "alfa",
                "pct_after": 12.0,
            },
            base
            | {
                "report_id": "2",
                "isin": "X",
                "published": date(2026, 1, 1),
                "holder": "Alfa SA",
                "holder_key": "alfa",
                "pct_after": 15.5,
            },
            base
            | {
                "report_id": "3",
                "isin": "X",
                "published": date(2025, 6, 1),
                "holder": "Beta",
                "holder_key": "beta",
                "pct_after": 7.0,
            },
            base
            | {
                "report_id": "4",
                "isin": "X",
                "published": date(2026, 2, 1),
                "holder": "Beta",
                "holder_key": "beta",
                "pct_after": 4.2,
                "direction": "down",
            },
            # a notice with no stake but "below 5 %" also takes the holder off
            base
            | {
                "report_id": "5",
                "isin": "X",
                "published": date(2025, 6, 1),
                "holder": "Gamma",
                "holder_key": "gamma",
                "pct_after": 9.0,
            },
            base
            | {
                "report_id": "6",
                "isin": "X",
                "published": date(2026, 3, 1),
                "holder": "Gamma",
                "holder_key": "gamma",
                "pct_after": None,
                "direction": "down",
                "threshold": 5.0,
            },
            # an old stake stays, marked stale
            base
            | {
                "report_id": "7",
                "isin": "X",
                "published": date(2019, 1, 1),
                "holder": "Delta",
                "holder_key": "delta",
                "pct_after": 20.0,
            },
        ]
    )
    reg = ow.register(obs, date(2026, 11, 12))
    got = {r["holder_key"]: (r["pct"], r["stale"]) for r in reg.iter_rows(named=True)}
    assert got == {"alfa": (15.5, False), "delta": (20.0, True)}
    # point in time: on 1 July 2025 Beta and Gamma still held their stakes
    early = ow.register(obs, date(2025, 7, 1))
    assert set(early["holder_key"]) == {"alfa", "beta", "gamma", "delta"}


def test_display_names_prefer_the_nominative_of_a_meeting_list():
    base = {
        "isin": "X",
        "pct_before": None,
        "pct_after": 10.0,
        "direction": None,
        "threshold": None,
        "notes": None,
        "holder_key": "jan kowal",
    }
    obs = _obs(
        [
            base
            | {
                "report_id": "1",
                "published": date(2026, 1, 1),
                "source": "meeting",
                "holder": "Jan Kowalski",
            },
            base
            | {
                "report_id": "2",
                "published": date(2026, 2, 1),
                "source": "notice",
                "holder": "Jana Kowalskiego",
            },
            base
            | {
                "report_id": "3",
                "published": date(2026, 2, 1),
                "source": "notice",
                "holder": "Nationale-Nederlanden OFE z siedzibą w Warszawie",
                "holder_key": "nn_ofe",
            },
        ]
    )
    names = ow.display_names(obs)
    assert names == {"jan kowal": "Jan Kowalski", "nn_ofe": "Nationale-Nederlanden OFE"}


def test_no_observations_gives_an_empty_register_and_page(tmp_path):
    empty = pl.DataFrame(schema={**ow.OBS_SCHEMA, "source_url": pl.String})
    reg = ow.register(empty, date(2026, 11, 12))
    assert reg.is_empty() and "stale" in reg.columns
    (tmp_path / "gold").mkdir()
    empty.write_parquet(tmp_path / "gold" / ow.GOLD_FILE)
    ctx = ow.context(tmp_path, date(2026, 11, 12), {})
    assert ctx is not None and ctx["recent"] == [] and ctx["index"] == []
    assert ctx["coverage"]["holders"] == 0
