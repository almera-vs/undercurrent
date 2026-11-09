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
    "Past 20-day excess return": "Historyczna nadwyżkowa stopa zwrotu (20 dni)",
    "Rose in {p} of {n} cases": "Wzrost w {p} z {n} przypadków",
    "Fell in {p} of {n} cases": "Spadek w {p} z {n} przypadków",
    "{n} past cases (too few for statistics)": (
        "wcześniejsze przypadki: {n} (za mało na statystykę)"
    ),
    "No event today has a signal type with enough history for statistics yet.": (
        "Żadne dzisiejsze zdarzenie nie ma jeszcze typu z historią wystarczającą do statystyk."
    ),
    "All reports today": "Wszystkie dzisiejsze raporty",
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
        "„Działa po kosztach” oznacza q po korekcie FDR poniżej 0,05 i dodatni wynik testu "
        "walk-forward po kosztach transakcyjnych 0,3% w obie strony. Wszystkie pozostałe wyniki "
        "też są pokazane, nic nie jest ukrywane."
    ),
    "Signal types tested": "Przetestowane typy sygnałów",
    "Hold up after costs": "Działają po kosztach",
    "Significant, but eaten by costs": "Istotne, ale zjadane przez koszty",
    "Noise": "Szum",
    "Signal library": "Biblioteka sygnałów",
    "Horizon": "Horyzont",
    "d": " dni",
    "Signal": "Sygnał",
    "Legal basis": "Podstawa prawna",
    "Events": "Zdarzenia",
    "Avg excess [0,+{h}]": "Śr. nadwyżka [0,+{h}]",
    "95% CI": "95% przedział",
    "Hit rate": "Odsetek wzrostów",
    "FDR q": "q (FDR)",
    "Walk-forward after costs": "Walk-forward po kosztach",
    "Deflated SR": "Deflated SR",
    "Verdict": "Werdykt",
    "{n} trades": "transakcje: {n}",
    "No statistics yet.": "Brak statystyk.",
    "signals_footnote": (
        "Nadwyżkowa stopa zwrotu względem WIG (model rynkowy, sesje −250…−30) na kursach "
        "skorygowanych kursami odniesienia publikowanymi przez giełdę (podziały, dywidendy, "
        "prawa poboru). Każde zdarzenie jest umieszczone w chwili, w której Undercurrent mógł "
        "się o nim dowiedzieć (known_at), więc nie ma zaglądania w przyszłość. Typy sygnałów z "
        "mniej niż 10 zdarzeniami nie mają statystyk."
    ),
    # pipeline
    "Unattended runs · GitHub Actions · weekdays 17:47 UTC": (
        "Przebiegi bez nadzoru · GitHub Actions · dni robocze 17:47 UTC"
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
    "ok": "ok",
    "degraded": "niepełny",
    "failed": "błąd",
    # event
    "What the report says": "Co mówi raport",
    "insider_note": (
        "Pola odczytane z treści raportu. Formularz powiadomienia (PDF w załączniku ESPI) jest "
        "chroniony przed automatycznym pobieraniem, więc nie jest czytany. „—” oznacza, że "
        "treść raportu tego nie podaje."
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
    "event_chart_note": (
        "Średnia nadwyżkowa stopa zwrotu względem WIG, od 5 sesji przed do 20 sesji po "
        "raporcie, z 95% pasmem bootstrap."
    ),
    "Avg 20-day excess return": "Śr. nadwyżka w 20 dni",
    "FDR-adjusted q": "q po korekcie FDR",
    "Walk-forward, after 0.3% costs": "Walk-forward, po kosztach 0,3%",
    "Worst case in sample": "Najgorszy przypadek w próbie",
    "Closest past events": "Najbliższe wcześniejsze zdarzenia",
    "Date": "Data",
    "Company": "Spółka",
    "20-day excess": "Nadwyżka 20 dni",
    "No past events of this type yet.": "Brak wcześniejszych zdarzeń tego typu.",
    # chart
    "Not enough past events for a chart yet.": "Za mało wcześniejszych zdarzeń na wykres.",
    "report published": "publikacja raportu",
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
        "“Holds up” means FDR-adjusted q below 0.05 and a positive result in the walk-forward "
        "test after 0.3% round-trip costs. Everything else is shown, not hidden."
    ),
    "signals_footnote": (
        "Excess return vs WIG (market model, days −250…−30) on prices adjusted with the "
        "exchange's own reference prices (splits, dividends, rights issues). Every event is "
        "placed at the moment Undercurrent could have known it (known_at), so there is no "
        "look-ahead. Signal types with fewer than 10 events show no statistics."
    ),
    "insider_note": (
        "Fields parsed from the report text. The notification form (a PDF attached to the "
        "ESPI report) is protected against automated download, so it is not read. “—” means "
        "the report text does not state it."
    ),
    "event_chart_note": (
        "Average excess return vs WIG, from 5 days before to 20 days after the report, with "
        "95% bootstrap band."
    ),
    "d": "d",
}

# Polish plural forms: (1, 2–4, 5+), keyed by the English singular.
PL_PLURALS: dict[str, tuple[str, str, str]] = {
    "event worth a look today": (
        "zdarzenie warte uwagi dziś",
        "zdarzenia warte uwagi dziś",
        "zdarzeń wartych uwagi dziś",
    ),
}
EN_PLURALS: dict[str, str] = {"event worth a look today": "events worth a look today"}

SIGNAL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "insider_tx:buy": "Insider buy",
        "insider_tx:sell": "Insider sell",
        "insider_tx:unknown": "Insider transaction (details in PDF)",
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
        "insider_tx:unknown": "Transakcja insidera (szczegóły w PDF)",
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
    "en": {"holds_up": "Holds up", "eaten_by_costs": "Eaten by costs", "noise": "Noise"},
    "pl": {
        "holds_up": "Działa po kosztach",
        "eaten_by_costs": "Zjadany przez koszty",
        "noise": "Szum",
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
    "en": {"off_market": "off market", "block_trade": "block trade"},
    "pl": {"off_market": "poza rynkiem", "block_trade": "transakcja pakietowa"},
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
    """``poniedziałek, 9 listopada 2026`` / ``Monday, 09 November 2026``."""
    if lang == "pl":
        return f"{_PL_DAYS[d.weekday()]}, {d.day} {_PL_MONTHS[d.month - 1]} {d.year}"
    return d.strftime("%A, %d %B %Y")


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
