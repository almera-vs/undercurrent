"""Site localisation: Polish (default, served at the site root) and English (``/en/``).

English source strings are the keys. ``tr(lang)`` returns a gettext-style function; Polish
plural forms (1 / 2–4 / 5+) go through ``plural``. Report titles and legal bases are never
translated: they are shown as the issuer published them, in Polish.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date
from typing import Any

LANGS = ("pl", "en")
DEFAULT_LANG = "pl"

PL: dict[str, str] = {
    # navigation, chrome
    "Today": "Dziś",
    "Signals": "Sygnały",
    "Pipeline": "Proces",
    "Undercurrent home": "Undercurrent, strona główna",
    "Fresh": "Aktualne",
    "Degraded": "Niepełne",
    "Failed": "Błąd",
    "Unknown": "Nieznany",
    "run": "przebieg",
    "Nightly GPW + NewConnect event radar. Not investment advice.": (
        "Wieczorny radar zdarzeń z GPW i NewConnect. To nie jest rekomendacja inwestycyjna."
    ),
    "Not investment advice. Historical statistics only.": (
        "To nie jest rekomendacja inwestycyjna. Wyłącznie statystyki historyczne."
    ),
    "Data: official GPW / NewConnect ESPI-EBI pages and quote archives.": (
        "Dane: oficjalne strony raportów ESPI/EBI oraz archiwa notowań GPW i NewConnect."
    ),
    "Source": "Kod źródłowy",
    # today
    "Ranked by how strongly this kind of event has moved prices in the past, after costs.": (
        "Kolejność według tego, jak mocno zdarzenia tego typu poruszały kursem w przeszłości, "
        "po kosztach."
    ),
    "History so far: {n} studied events since {since}.": (
        "Dotychczasowa historia: zbadane zdarzenia: {n}, od {since}."
    ),
    "Reports ingested": "Pobrane raporty",
    "Classified": "Sklasyfikowane",
    "{n} queued for retry": "w kolejce do ponowienia: {n}",
    "Insider transactions": "Transakcje insiderów",
    "{b} buys · {s} sells stated (Art. 19 MAR)": "zakupy: {b} · sprzedaże: {s} (art. 19 MAR)",
    "Holder thresholds crossed": "Przekroczone progi akcjonariatu",
    "Art. 69 Public Offering Act": "Art. 69 ustawy o ofercie publicznej",
    "Worth a look": "Warte uwagi",
    "Past excess return, {h} sessions after entry": (
        "Historyczna nadwyżka w ciągu {h} sesji od wejścia"
    ),
    "Rose in {p} of {n} cases": "Wzrost w {p} z {n} przypadków",
    "Fell in {p} of {n} cases": "Spadek w {p} z {n} przypadków",
    "{n} past cases (too few for statistics)": (
        "wcześniejsze przypadki: {n} (za mało na statystykę)"
    ),
    "No event today is of a type that has moved prices significantly in the past.": (
        "Żadne dzisiejsze zdarzenie nie należy do typu, który w przeszłości istotnie "
        "poruszał kursem."
    ),
    "Skip to content": "Przejdź do treści",
    "Reports in this digest": "Raporty w tym podsumowaniu",
    "Filter reports by type": "Filtruj raporty według typu",
    "All": "Wszystkie",
    "Insider": "Insiderzy",
    "Inside info": "Inf. poufne",
    "Holdings": "Akcjonariat",
    "Other": "Inne",
    "No reports in this window.": "Brak raportów w tym okresie.",
    "Tonight's run": "Dzisiejszy przebieg",
    "Ingest {n} new reports": "Nowe raporty: {n}",
    "{n} failed, queued for retry": "Nieudane: {n}, w kolejce do ponowienia",
    "Prices: GPW {g} rows, NC {nc}": "Kursy (wiersze): GPW {g}, NC {nc}",
    "pending": "w oczekiwaniu",
    "{n} rows": "{n}",
    "dbt build + data tests, 0 unexplained jumps": (
        "dbt build + testy danych, 0 niewyjaśnionych skoków"
    ),
    "Event studies rebuilt": "Analizy zdarzeń przeliczone",
    # signals
    "Rebuilt nightly": "Przeliczane co wieczór",
    "{n} studied events": "zbadane zdarzenia: {n}",
    "Which signals actually hold up": "Które sygnały naprawdę działają",
    "signals_lede": (
        "„Działa po kosztach” oznacza q po korekcie FDR poniżej 0,05 oraz wynik testu "
        "walk-forward po kosztach transakcyjnych w obie strony (2,0% na GPW, 5,2% na "
        "NewConnect), który jest dodatni i sam istotny statystycznie. Wszystkie pozostałe "
        "wyniki też są pokazane, nic nie jest ukrywane."
    ),
    "Signal types tested": "Przetestowane typy sygnałów",
    "Hold up after costs": "Działają po kosztach",
    "Not confirmed out of sample": "Niepotwierdzone poza próbą",
    "Significant, but eaten by costs": "Istotne, ale zjadane przez koszty",
    "Noise": "Szum",
    "Signal library": "Biblioteka sygnałów",
    "Horizon": "Horyzont",
    "Signal": "Sygnał",
    "Legal basis": "Podstawa prawna",
    "Scroll sideways for all columns.": "Przewiń w bok, aby zobaczyć wszystkie kolumny.",
    "Events": "Zdarzenia",
    "Avg excess [0,+{h}]": "Śr. nadwyżka w ciągu {h} sesji od wejścia",
    "95% CI": "95% przedział",
    "Hit rate": "Odsetek wzrostów",
    "FDR q": "q (FDR)",
    "Walk-forward after costs": "Walk-forward po kosztach",
    "Deflated SR": "Deflated SR",
    "Verdict": "Werdykt",
    "{n} trades": "transakcje: {n}",
    "No statistics yet.": "Brak statystyk.",
    "signals_footnote": (
        "Nadwyżkowa stopa zwrotu względem WIG (składana stopa zwrotu akcji minus WIG) "
        "na kursach skorygowanych kursami odniesienia publikowanymi przez giełdę (podziały, "
        "dywidendy, "
        "prawa poboru). Każde zdarzenie jest umieszczone w chwili, w której Undercurrent mógł "
        "się o nim dowiedzieć (known_at), więc nie ma zaglądania w przyszłość. Wejście to "
        "zamknięcie pierwszej sesji po wieczornym podsumowaniu, które jako pierwsze podało "
        "raport; ruch ceny przed tym momentem nie jest liczony. Typy sygnałów z mniej niż 10 "
        "zdarzeniami nie mają statystyk."
    ),
    # pipeline
    "Unattended runs · GitHub Actions · weekdays 18:47 Warsaw time (19:47 in summer)": (
        "Przebiegi bez nadzoru · GitHub Actions · dni robocze 18:47 czasu warszawskiego "
        "(latem 19:47)"
    ),
    "Pipeline health": "Stan procesu",
    "Freshness SLO: digest sent by 20:30 Warsaw time on every trading day.": (
        "Cel: podsumowanie wysłane do 20:30 czasu warszawskiego w każdy dzień sesyjny."
    ),
    "Run date": "Data przebiegu",
    "Status": "Status",
    "Finished (Warsaw)": "Zakończenie (Warszawa)",
    "Within SLO": "W terminie",
    "New reports": "Nowe raporty",
    "GPW rows": "Wiersze GPW",
    "NC rows": "Wiersze NC",
    "Notes": "Uwagi",
    "Failed reports": "Nieudane",
    "No runs recorded yet.": "Brak zapisanych przebiegów.",
    "{n} earlier run(s) replaced": "zastąpione wcześniejsze przebiegi: {n}",
    "ok": "ok",
    "degraded": "niepełny",
    "failed": "błąd",
    # event
    "What the report says": "Co mówi raport",
    "insider_note": (
        "Pola odczytane z treści raportu: kopii formularza powiadomienia nie znaleziono w "
        "serwisach parkiet.com, bankier.pl ani biznesradar.pl. „—” oznacza, że treść raportu "
        "tego nie podaje."
    ),
    "form_note": (
        "Pola z formularza powiadomienia MAR załączonego do raportu, odczytane z kopii w "
        "serwisie {portal}. Czego formularz nie podaje, uzupełnia treść raportu. „—” oznacza, "
        "że nie podaje tego ani formularz, ani raport."
    ),
    "Person": "Osoba",
    "Role": "Funkcja",
    "Transaction": "Transakcja",
    "Volume": "Wolumen",
    "Avg price": "Średnia cena",
    "Value": "Wartość",
    "Trade date": "Data transakcji",
    "Size vs free float": "Udział w free float",
    "(no free-float data)": "(brak danych o free float)",
    "Issuer": "Emitent",
    "Published {t} (Warsaw)": "Opublikowano {t} (czas warszawski)",
    "Known to Undercurrent {t} (known_at)": "Znane Undercurrent od {t} (known_at)",
    "Original report ↗": "Oryginalny raport ↗",
    "What happened after {n} similar events": "Co działo się po podobnych zdarzeniach (n = {n})",
    "What happened after similar events": "Co działo się po podobnych zdarzeniach",
    "event_chart_note": (
        "Średnia nadwyżkowa stopa zwrotu względem WIG, od 5 sesji przed do 20 sesji po "
        "raporcie, z 95% pasmem bootstrap. Dzień 0 to pierwsze zamknięcie po publikacji; "
        "statystyki liczą się dopiero od zamknięcia sesji po podsumowaniu."
    ),
    "Avg excess, {h} sessions after entry": "Śr. nadwyżka w ciągu {h} sesji od wejścia",
    "FDR-adjusted q": "q po korekcie FDR",
    "Walk-forward, after costs": "Walk-forward, po kosztach",
    "Worst case in sample": "Najgorszy przypadek w próbie",
    "Worst case for a short, in sample": "Najgorszy przypadek dla krótkiej pozycji w próbie",
    "Most recent past events": "Ostatnie wcześniejsze zdarzenia",
    "Date": "Data",
    "Company": "Spółka",
    "{h}-session excess": "Nadwyżka w ciągu {h} sesji",
    "No past events of this type yet.": "Brak wcześniejszych zdarzeń tego typu.",
    # chart
    "Not enough past events for a chart yet.": "Za mało wcześniejszych zdarzeń na wykres.",
    "report published": "publikacja raportu",
    "day 0: first close after publication": "dzień 0: pierwsze zamknięcie po publikacji",
    "entry for this report: close of the session after the digest (day {d})": (
        "wejście dla tego raportu: zamknięcie sesji po podsumowaniu (dzień {d})"
    ),
    "day 0": "dzień 0",
    "Average excess return path, day -5 to +20": (
        "Ścieżka średniej nadwyżkowej stopy zwrotu, dni od -5 do +20"
    ),
    # health line
    "Run {s}": "Przebieg: {s}",
    "NewConnect prices pending": "kursy NewConnect w oczekiwaniu",
    "{n} reports failed 5+ times": "raporty nieudane 5+ razy: {n}",
    "history since {d}": "historia od {d}",
    # page titles
    "Today · Undercurrent": "Dziś · Undercurrent",
    "Signals · Undercurrent": "Sygnały · Undercurrent",
    "Pipeline · Undercurrent": "Proces · Undercurrent",
}

EN_OVERRIDES: dict[str, str] = {
    "signals_lede": (
        "“Holds up” means FDR-adjusted q below 0.05 and a walk-forward result after "
        "round-trip costs (2.0% on GPW, 5.2% on NewConnect) that is positive and itself "
        "statistically significant. Everything else is shown, not hidden."
    ),
    "signals_footnote": (
        "Excess return vs WIG (the stock's compounded return minus the index's) on prices "
        "adjusted with the exchange's own reference prices (splits, dividends, rights "
        "issues). Every event is "
        "placed at the moment Undercurrent could have known it (known_at), so there is no "
        "look-ahead. Entry is the close of the first session after the evening digest that "
        "first carried the report; the move before that is not counted. Signal types with "
        "fewer than 10 events show no statistics."
    ),
    "insider_note": (
        "Fields parsed from the report text: no copy of the notification form was found on "
        "parkiet.com, bankier.pl or biznesradar.pl. “—” means the report text does not "
        "state it."
    ),
    "form_note": (
        "Fields from the MAR notification form attached to the report, read from the copy on "
        "{portal}. Where the form is silent, the report text fills in. “—” means neither "
        "states it."
    ),
    "event_chart_note": (
        "Average excess return vs WIG, from 5 days before to 20 days after the report, with "
        "95% bootstrap band. Day 0 is the first close after publication; the statistics start "
        "at the close of the session after the digest."
    ),
    "Avg excess [0,+{h}]": "Avg excess, {h} sessions after entry",
}

# Polish plural forms: (1, 2–4, 5+), keyed by the English singular.
PL_PLURALS: dict[str, tuple[str, str, str]] = {
    "event worth a look today": (
        "zdarzenie warte uwagi dziś",
        "zdarzenia warte uwagi dziś",
        "zdarzeń wartych uwagi dziś",
    ),
    "session": ("sesja", "sesje", "sesji"),
}
EN_PLURALS: dict[str, str] = {
    "event worth a look today": "events worth a look today",
    "session": "sessions",
}

SIGNAL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "insider_tx:buy": "Insider buy",
        "insider_tx:sell": "Insider sell",
        "insider_tx:unknown": "Insider transaction (other or not stated)",
        "holder_threshold": "Holder threshold crossed",
        "inside_info:profit_warning": "Results estimate / profit warning",
        "inside_info:large_contract": "Large contract",
        "inside_info:buyback": "Buyback",
        "inside_info:dividend_change": "Dividend decision",
        "inside_info:m_and_a": "M&A / tender offer",
        "inside_info:financing": "Financing (bonds, loans, issues)",
        "inside_info:strategy": "Strategy",
        "inside_info:litigation": "Litigation",
        "inside_info:rating": "Rating",
        "inside_info:other": "Other inside information",
        "other": "Other",
        "periodic": "Periodic",
    },
    "pl": {
        "insider_tx:buy": "Zakup przez insidera",
        "insider_tx:sell": "Sprzedaż przez insidera",
        "insider_tx:unknown": "Transakcja insidera (inna lub nieokreślona)",
        "holder_threshold": "Przekroczenie progu akcjonariatu",
        "inside_info:profit_warning": "Szacunek wyników / ostrzeżenie",
        "inside_info:large_contract": "Znacząca umowa",
        "inside_info:buyback": "Skup akcji własnych",
        "inside_info:dividend_change": "Decyzja o dywidendzie",
        "inside_info:m_and_a": "Fuzje, przejęcia, wezwania",
        "inside_info:financing": "Finansowanie (obligacje, kredyty, emisje)",
        "inside_info:strategy": "Strategia",
        "inside_info:litigation": "Spory sądowe",
        "inside_info:rating": "Rating",
        "inside_info:other": "Inne informacje poufne",
        "other": "Inne",
        "periodic": "Okresowy",
    },
}
BASIS_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "insider_tx": "Art. 19 MAR",
        "holder_threshold": "Art. 69 POA",
        "inside_info": "Art. 17 MAR",
    },
    "pl": {
        "insider_tx": "Art. 19 MAR",
        "holder_threshold": "Art. 69 ustawy o ofercie",
        "inside_info": "Art. 17 MAR",
    },
}
VERDICT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "holds_up": "Holds up",
        "unconfirmed": "Not confirmed out of sample",
        "eaten_by_costs": "Eaten by costs",
        "untested": "Not yet tested",
        "noise": "Noise",
        "too_few": "Too few events",
    },
    "pl": {
        "holds_up": "Działa po kosztach",
        "unconfirmed": "Niepotwierdzony poza próbą",
        "eaten_by_costs": "Zjadany przez koszty",
        "untested": "Jeszcze nie przetestowany",
        "noise": "Szum",
        "too_few": "Za mało zdarzeń",
    },
}
ROLE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "CEO": "CEO",
        "CFO": "CFO",
        "board": "board member",
        "supervisory_board": "supervisory board member",
        "closely_associated": "closely associated person",
        "pdmr": "person with managerial duties",
    },
    "pl": {
        "CEO": "prezes zarządu",
        "CFO": "dyrektor finansowy",
        "board": "członek zarządu",
        "supervisory_board": "członek rady nadzorczej",
        "closely_associated": "osoba blisko związana",
        "pdmr": "osoba pełniąca obowiązki zarządcze",
    },
}
SIDE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "buy": "buy",
        "sell": "sell",
        "mixed": "buy and sell",
        "pledge": "pledge",
        "gift": "gift",
        "incentive": "incentive-plan grant",
    },
    "pl": {
        "buy": "zakup",
        "sell": "sprzedaż",
        "mixed": "zakup i sprzedaż",
        "pledge": "zastaw",
        "gift": "darowizna",
        "incentive": "przydział w programie motywacyjnym",
    },
}
VENUE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "off_market": "off market",
        "block_trade": "block trade",
        "GPW_derivatives": "GPW derivatives",
        "other": "other",
    },
    "pl": {
        "off_market": "poza rynkiem",
        "block_trade": "transakcja pakietowa",
        "GPW_derivatives": "GPW, rynek derywatów",
        "other": "inne",
    },
}

_PL_DAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
_PL_MONTHS = (
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
)  # fmt: skip


def tr(lang: str) -> Callable[..., str]:
    """``_('English text', **kw)`` for ``lang``; unknown strings fall back to English."""
    table = PL if lang == "pl" else EN_OVERRIDES

    def _(msg: str, /, **kw: Any) -> str:
        out = table.get(msg, msg)
        return out.format(**kw) if kw else out

    return _


def plural(lang: str, n: int, key: str) -> str:
    """Count-dependent phrase: Polish 1 / 2–4 / 5+ forms, English singular / plural."""
    if lang == "pl":
        one, few, many = PL_PLURALS[key]
        if n == 1:
            return one
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return few
        return many
    return key if n == 1 else EN_PLURALS[key]


def long_date(d: date, lang: str) -> str:
    """``poniedziałek, 9 listopada 2026`` / ``Monday, 9 November 2026``."""
    if lang == "pl":
        return f"{_PL_DAYS[d.weekday()]}, {d.day} {_PL_MONTHS[d.month - 1]} {d.year}"
    return f"{d:%A}, {d.day} {d:%B %Y}"


def _bad(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def group_digits(s: str, lang: str) -> str:
    """Swap English separators for Polish ones (NBSP thousands, decimal comma)."""
    if lang != "pl":
        return s
    return s.replace(",", "\xa0").replace(".", ",")


def pct(x: float | None, lang: str = "en", digits: int = 1, sign: bool = True) -> str:
    """``0.031`` → ``+3.1%`` (en) / ``+3,1%`` (pl); ``None`` → ``—``."""
    if _bad(x):
        return "—"
    assert x is not None
    if round(x * 100, digits) == 0:  # never "-0.0%" or "+0.0%"
        return group_digits(f"{0:.{digits}f}%", lang)
    return group_digits(f"{x * 100:{'+' if sign else ''}.{digits}f}%", lang)


def num(x: float | None, lang: str = "en") -> str:
    """Grouped number, two decimals below 100 unless whole; ``—`` for missing."""
    if _bad(x):
        return "—"
    assert x is not None
    s = f"{x:,.0f}" if abs(x) >= 100 or float(x).is_integer() else f"{x:,.2f}"
    return group_digits(s, lang)


def short_pln(x: float | None, lang: str = "en") -> str:
    """``772800`` → ``773k`` (en) / ``773 tys.`` (pl)."""
    if x is None:
        return "—"
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}m" if lang != "pl" else f"{x / 1e6:.1f} mln".replace(".", ",")
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k" if lang != "pl" else f"{x / 1e3:.0f} tys."
    return f"{x:.0f}"
