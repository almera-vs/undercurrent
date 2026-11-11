"""Report text, plain-language summaries and the company context on the event pages."""

from datetime import date, timedelta

import pytest

from undercurrent import pages, publish


def test_report_text_artefacts_are_repaired():
    raw = 'Zarząd X S.A. _"Spółka"_ informuje (?Emitent?) o umowie _słownie: sto_.'
    assert pages.clean_report_text(raw) == (
        'Zarząd X S.A. ("Spółka") informuje („Emitent”) o umowie (słownie: sto).'
    )


def test_lead_stops_at_a_sentence_end_not_an_abbreviation():
    first = "Zarząd Spółki Y S.A. informuje, że w dniu 10 listopada 2026 r. zawarł umowę " * 3
    text = first.strip() + ". Druga sprawa jest mniej ważna. " + "Dalej. " * 100
    lead = pages.report_lead(text)
    assert lead.endswith("zawarł umowę.") and "Druga" not in lead
    assert pages.report_lead("Krótki raport.") == "Krótki raport."


def test_money_amounts_in_order_without_repeats():
    text = "Wartość 820 000zł rocznie, pożyczka 121.689 zł, 5 mln zł, PLN 34.41, znów 5 mln zł."
    assert pages.money_amounts(text) == ["820 000 zł", "121.689 zł", "5 mln zł", "PLN 34.41"]


def test_plain_summary_says_it_without_jargon():
    st = {"mean_car": -0.047, "hit_rate": 0.36, "n": 3590}
    pl_text = pages.plain_summary(st, 60, "pl")
    assert "4,7 pkt proc. gorzej niż WIG" in pl_text and "64% z 3\xa0590" in pl_text
    assert "ok. trzy miesiące" in pl_text
    en = pages.plain_summary({"mean_car": 0.02, "hit_rate": 0.55, "n": 40}, 5, "en")
    assert "2.0 pp better than WIG" in en and "55% of 40" in en
    assert "Too few" in pages.plain_summary(None, 20, "en")


def _hist(n: int, close: float, turnover: float, ret: float = 0.01) -> pages.PriceHistory:
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    return pages.PriceHistory(
        days, [close] * n, [close * (1 + ret) ** i for i in range(n)], [ret] * n, [turnover] * n
    )


def test_liquidity_uses_the_study_rule():
    ok = _hist(80, 12.0, 50_000.0)
    assert pages.liquidity(ok, ok.dates[70])[2] is True
    penny = _hist(80, 0.02, 50_000.0)
    assert pages.liquidity(penny, penny.dates[70])[2] is False
    assert pages.liquidity(ok, ok.dates[2]) == (None, None, None)


def test_realised_excess_compounds_against_wig_up_to_the_horizon():
    h = _hist(30, 10.0, 1e5, ret=0.01)
    wig = {d: 0.0 for d in h.dates}
    val, n = pages.realised_excess(h, wig, h.dates, h.dates[9], 5)
    assert n == 5 and val == pytest.approx(1.01**5 - 1)
    val, n = pages.realised_excess(h, wig, h.dates, h.dates[-1], 5)
    assert (val, n) == (None, 0)  # nothing after entry yet
    h.ret[12] = None  # the stock did not trade
    assert pages.realised_excess(h, wig, h.dates, h.dates[9], 5)[0] is None


def test_today_groups_events_by_verdict_then_signal():
    def ev(key: str, verdict: str, i: int) -> dict:
        return {"signal_key": key, "stat": {"verdict": verdict}, "horizon": 20, "report_id": i}

    worth = [
        ev("a", "holds_up", 1),
        ev("b", "eaten_by_costs", 2),
        ev("b", "eaten_by_costs", 3),
        ev("c", "eaten_by_costs", 4),
    ]
    tiers = publish._tiers(worth)
    assert [t["verdict"] for t in tiers] == ["holds_up", "eaten_by_costs"]
    assert [g["key"] for g in tiers[1]["groups"]] == ["b", "c"]
    assert [e["report_id"] for e in tiers[1]["groups"][0]["events"]] == [2, 3]
    assert tiers[1]["n"] == 3


def test_chart_labels_publication_entry_and_horizon_end():
    path = [{"day": d, "mean_car": 0.001 * d, "lo": -0.01, "hi": 0.01} for d in range(-5, 21)]
    svg = publish.car_chart_svg(path, entry=1, horizon=5, lang="pl")
    assert "publikacja" in svg and "wejście" in svg and "koniec 5 sesji" in svg
    assert 'class="horizon"' not in publish.car_chart_svg(path, entry=1, horizon=60)


def test_company_names_keep_their_legal_form():
    assert pages.company_name("MILTON ESSEX S.A.") == "Milton Essex S.A."
    assert pages.company_name("Milton Essex S.a.") == "Milton Essex S.A."
    assert pages.company_name("EQUNICO SE") == "Equnico SE"
    assert pages.company_name("Grupa Azoty Spółka Akcyjna") == "Grupa Azoty S.A."
    assert pages.company_name(None) == ""
