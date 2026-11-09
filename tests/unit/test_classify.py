from datetime import date
from pathlib import Path

import pytest

from undercurrent.classify import classify
from undercurrent.ingest import extract_report_page
from undercurrent.parse import extract_insider_fields

FIX = Path(__file__).parents[1] / "fixtures" / "pages"


@pytest.mark.parametrize(
    ("basis", "title", "kind", "expected", "sub"),
    [
        (
            "Art. 19 ust. 3 Rozporządzenia MAR - informacja o transakcjach wykonywanych przez "
            "osoby pełniące obowiązki zarządcze",
            "Informacja o transakcjach",
            "Bieżący",
            "insider_tx",
            None,
        ),
        (
            "Art. 160 ust. 4 Ustawy o obrocie instrumentami finansowymi",
            None,
            "Bieżący",
            "insider_tx",
            None,
        ),
        (
            "Art. 70 pkt 1 Ustawy o ofercie - nabycie lub zbycie znacznego pakietu akcji",
            "Informacja o transakcjach na akcjach KRUK S.A.",
            "Bieżący",
            "holder_threshold",
            None,
        ),
        (
            "Art. 69 ust. 1 pkt 2 Ustawy o ofercie publicznej",
            None,
            "Bieżący",
            "holder_threshold",
            None,
        ),
        (
            "Art. 17 ust. 1 MAR - informacje poufne.",
            "Rekomendacja Zarządu w sprawie wypłaty dywidendy",
            "Bieżący",
            "inside_info",
            "dividend_change",
        ),
        (
            "Art. 17 ust. 1 MAR - informacje poufne.",
            "Zawarcie znaczącej umowy z PKP PLK",
            "Bieżący",
            "inside_info",
            "large_contract",
        ),
        (
            "Art. 17 ust. 1 MAR - informacje poufne.",
            "Szacunkowe wyniki za III kwartał 2026",
            "Bieżący",
            "inside_info",
            "profit_warning",
        ),
        (
            "Art. 17 ust. 1 MAR - informacje poufne.",
            "Rozpoczęcie skupu akcji własnych",
            "Bieżący",
            "inside_info",
            "buyback",
        ),
        (
            "Art. 17 ust. 1 MAR - informacje poufne.",
            "Zmiana w składzie",
            "Bieżący",
            "inside_info",
            "other",
        ),
        (
            "Art. 56 ust. 1 pkt 2 Ustawy o ofercie - informacje bieżące i okresowe",
            "Zwołanie NWZ",
            "Bieżący",
            "other",
            None,
        ),
        (None, "Raport kwartalny", "Kwartalny", "periodic", None),
        (
            "§ 4 ust. 2 Załącznika nr 3 do Regulaminu ASO",
            "Ogłoszenie o zwołaniu NWZ",
            "Bieżący",
            "other",
            None,
        ),
    ],
)
def test_classify_cases(basis, title, kind, expected, sub):
    c = classify(basis, title, None, kind)
    assert c.signal_type == expected
    assert c.subtype == sub


def test_classify_text_fallback_without_basis():
    c = classify(None, "Zawiadomienie o zmianie stanu posiadania akcji", None, "Bieżący")
    assert c.signal_type == "holder_threshold" and c.rule == "text:holder"


def test_classify_is_pure():
    args = ("Art. 17 ust. 1 MAR", "Zawarcie umowy", "body", "Bieżący")
    assert classify(*args) == classify(*args)


def test_insider_fields_grodno():
    p = extract_report_page((FIX / "gpw_500131_espi_legacy_insider.html").read_text("utf-8"))
    f = extract_insider_fields("Otrzymanie powiadomienia w trybie art. 19 MAR", p.body_text)
    assert f.person == "Adama Nowickiego"
    assert f.role == "board"  # Wiceprezes Zarządu
    assert f.side is None and f.volume is None  # details only in the PDF attachment
    assert "side not stated (details only in attachment)" in f.notes


def test_insider_fields_closely_associated():
    p = extract_report_page((FIX / "gpw_500136_espi_legacy_insider.html").read_text("utf-8"))
    f = extract_insider_fields(None, p.body_text)
    assert f.role == "closely_associated"


def test_insider_fields_inline_details():
    body = (
        "Zarząd Spółki informuje, że w dniu 5 listopada 2026 r. otrzymał od Pana Jana Kowalskiego"
        " – Prezesa Zarządu – powiadomienie o nabyciu w dniu 4 listopada 2026 r. 12 500 akcji"
        " Spółki po średniej cenie 23,45 zł, o łącznej wartości 293 125,00 zł. Transakcja"
        " została zawarta na Giełdzie Papierów Wartościowych w Warszawie."
    )
    f = extract_insider_fields("Transakcje osoby pełniącej obowiązki zarządcze", body)
    assert f.person == "Jana Kowalskiego"
    assert f.role == "CEO"
    assert f.side == "buy"
    assert f.volume == 12500
    assert f.avg_price == 23.45
    assert f.value == 293125.0
    assert f.trade_date == date(2026, 11, 4)
    assert f.venue == "GPW"
    assert f.pct_free_float is None
