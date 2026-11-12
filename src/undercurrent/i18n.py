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
    "Archive": "Archiwum",
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
    "Unattended runs · GitHub Actions · weekdays, soon after the 18:30 Warsaw cut-off": (
        "Przebiegi bez nadzoru · GitHub Actions · dni robocze, krótko po 18:30 czasu warszawskiego"
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
    "running": "nie ukończono",
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
        "Średni wynik akcji względem WIG, od 5 sesji przed do 20 sesji po dniu 0, z 95% "
        "pasmem niepewności. Liczby pod wykresem dotyczą innego okna: {h} sesji od wejścia "
        "(zamknięcia sesji po podsumowaniu), bo tylko od tej chwili czytelnik mógł kupić."
    ),
    "Avg excess, {h} sessions after entry": "Śr. nadwyżka w ciągu {h} sesji od wejścia",
    "FDR-adjusted q": "q po korekcie FDR",
    "Walk-forward, after costs": "Walk-forward, po kosztach",
    "Bad case (1 in 20), in sample": "Zły scenariusz (1 na 20) w próbie",
    "Bad case for a short (1 in 20), in sample": (
        "Zły scenariusz dla krótkiej pozycji (1 na 20) w próbie"
    ),
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
    "exchange closed ({h})": "giełda nieczynna ({h})",
    # exchange holidays (gpw_calendar)
    "New Year's Day": "Nowy Rok",
    "Epiphany": "Trzech Króli",
    "Good Friday": "Wielki Piątek",
    "Easter Monday": "Poniedziałek Wielkanocny",
    "Labour Day": "Święto Pracy",
    "Constitution Day": "Święto Konstytucji 3 Maja",
    "Corpus Christi": "Boże Ciało",
    "Assumption Day": "Wniebowzięcie NMP",
    "All Saints' Day": "Wszystkich Świętych",
    "Independence Day": "Święto Niepodległości",
    "Christmas Eve": "Wigilia",
    "Christmas Day": "Boże Narodzenie",
    "Boxing Day": "drugi dzień Świąt",
    "New Year's Eve": "Sylwester",
    "trading system change": "zmiana systemu transakcyjnego",
    "MiFID II start": "start MiFID II",
    "Independence centenary": "stulecie niepodległości",
    # archive
    "Archive · Undercurrent": "Archiwum · Undercurrent",
    "Last {n} sessions": "Ostatnie sesje: {n}",
    "{n} events": "zdarzenia: {n}",
    "Earlier digests": "Wcześniejsze podsumowania",
    "archive_lede": (
        "Zdarzenia z wcześniejszych podsumowań, od najnowszego, z wynikiem względem WIG od "
        "wejścia do dziś. Statystyki na stronach zdarzeń są dzisiejsze, a nie z dnia "
        "publikacji."
    ),
    "Find a company": "Znajdź spółkę",
    "Ticker or name": "Ticker lub nazwa",
    "No event matches.": "Żadne zdarzenie nie pasuje.",
    "No earlier digests yet.": "Nie ma jeszcze wcześniejszych podsumowań.",
    # today, grouped by verdict
    "today_lede_none_hold": (
        "Raporty dziś w typach, które w przeszłości wyraźnie poruszały kursem. Żaden z tych "
        "typów nie zarabiał jednak po kosztach transakcji na danych, których nie użyto do jego "
        "wyboru: to wiedza o tym, co zwykle dzieje się z kursem, a nie sygnał do handlu."
    ),
    "today_lede_some_hold": (
        "Raporty dziś w typach, które w przeszłości wyraźnie poruszały kursem. Zdarzenia "
        "typów, które zarabiały także po kosztach na nowych danych ({k}), są na górze."
    ),
    "today_lede_empty": (
        "Dziś nie ma raportów w typach, które w przeszłości wyraźnie poruszały kursem. "
        "Wszystkie raporty są w kolumnie obok."
    ),
    "How to read this page": "Jak czytać tę stronę",
    "Inside information": "Informacje poufne",
    "Art. 17 MAR: contracts, financing, results…": "Art. 17 MAR: umowy, finansowanie, wyniki…",
    "Types that earned after costs": "Typy zarabiające po kosztach",
    "tested on new data, after trading costs": "sprawdzone na nowych danych, po kosztach",
    "thin_title": (
        "Mediana kursu poniżej 0,50 zł lub mediana obrotu poniżej 10 tys. zł dziennie przez "
        "60 sesji przed raportem. Takich spółek nie ma w statystykach."
    ),
    "Too thin to trade": "Za mała płynność",
    "{n} reports in this digest": "Raporty w podsumowaniu: {n}",
    # event page
    "thin_warning": (
        "Ta spółka jest zbyt tania lub zbyt rzadko handlowana (mediana kursu poniżej 0,50 zł "
        "albo obrotu poniżej 10 tys. zł dziennie). Takich spółek nie ma w statystykach, więc "
        "liczby powyżej jej nie dotyczą."
    ),
    "Size vs a usual day": "Wielkość wobec zwykłej sesji",
    "of median daily turnover": "mediany dziennego obrotu",
    "Full text of the report": "Pełna treść raportu",
    "The report has no text on the exchange page; its content is in the attachments.": (
        "Raport nie ma treści na stronie giełdy; jego zawartość jest w załącznikach."
    ),
    "Amounts named in the report": "Kwoty w raporcie",
    "In the digest of {d}": "W podsumowaniu z {d}",
    "The company": "Spółka",
    "Last close": "Ostatnie zamknięcie",
    "Reaction on day 0": "Reakcja w dniu 0",
    "WIG {w}": "WIG {w}",
    "Since entry": "Od wejścia",
    "vs WIG, {n} of {h} sessions": "względem WIG, {n} z {h} sesji",
    "not yet: entry is the close after the digest": (
        "jeszcze nie: wejście to zamknięcie sesji po podsumowaniu"
    ),
    "Liquidity": "Płynność",
    "median daily turnover": "mediana dziennego obrotu",
    "Tradable": "Płynna",
    "Share price, last 90 sessions (adjusted); the line marks day 0 of this report.": (
        "Kurs akcji z ostatnich 90 sesji (skorygowany); linia oznacza dzień 0 tego raportu."
    ),
    "No price data for this company yet.": "Brak notowań tej spółki.",
    "{t} on XTB ↗": "{t} w XTB ↗",
    "xtb_note": (
        "Strona instrumentu w XTB, sprawdzona po kodzie ISIN; logo pochodzi z serwera XTB i "
        "jest znakiem towarowym spółki. Undercurrent nie jest powiązany z XTB."
    ),
    "Other reports of this company": "Inne raporty tej spółki",
    "Avg vs WIG, {p} after entry": "Śr. wobec WIG, {p} od wejścia",
    "Better than WIG": "Lepiej niż WIG",
    "of {n} past cases": "z {n} wcześniejszych przypadków",
    "Traded on new data, after costs": "Handel na nowych danych, po kosztach",
    "wf_title": (
        "Reguła wybrana tylko z wcześniejszych lat, sprawdzona na kolejnym roku, po kosztach "
        "kupna i sprzedaży. Średni wynik jednej transakcji."
    ),
    "What is this?": "Co to jest?",
    "per trade": "na transakcję",
    "Chance it is luck": "Szansa, że to przypadek",
    "q_title": (
        "q po korekcie FDR: jak łatwo taki wynik wyszedłby przypadkiem, przy tylu "
        "sprawdzanych typach sygnałów. Poniżej 0,05 uznajemy ruch za prawdziwy."
    ),
    "tail_title": (
        "Tylko 1 na 20 wcześniejszych zdarzeń wypadło gorzej (dla krótkiej pozycji: kurs "
        "wzrósł bardziej)."
    ),
    "vs WIG over {h} sessions": "Wobec WIG w ciągu {h} sesji",
    "publication": "publikacja",
    "entry": "wejście",
    "end of {h} sessions": "koniec {h} sesji",
    # archive
    "How these events have done since entry": "Jak te zdarzenia wypadły od wejścia",
    "record_note": (
        "Zdarzenia z tego archiwum, od zamknięcia sesji po podsumowaniu do dziś (najwyżej "
        "tyle sesji, ile wynosi horyzont ich typu), względem WIG. Jedno zdarzenie na spółkę, "
        "typ i dzień. Większość okien jeszcze trwa, a próba jest mała: to wczesny odczyt, "
        "nie wynik."
    ),
    "Kind of signal": "Rodzaj sygnału",
    "Avg vs WIG so far": "Śr. wobec WIG do dziś",
    "vs WIG since entry, {n} sessions": "względem WIG od wejścia, sesje: {n}",
    "ses.": "ses.",
    # glossary
    "Glossary · Undercurrent": "Słowniczek · Undercurrent",
    "How to read Undercurrent": "Jak czytać Undercurrent",
    "Glossary": "Słowniczek",
    "glossary_lede": (
        "Każda liczba na stronie odpowiada na pytanie: co działo się z kursem spółki po "
        "takich raportach w przeszłości, w porównaniu z całym rynkiem."
    ),
    "Better or worse than WIG": "Lepiej lub gorzej niż WIG",
    "g_excess": (
        "Wynik akcji minus wynik indeksu WIG w tym samym czasie (obie stopy składane). "
        "„4,7 pkt proc. gorzej niż WIG” znaczy: gdy WIG zyskał 2%, akcja straciła 2,7%. "
        "Odejmowanie rynku pokazuje, co zrobił sam raport, a nie cała giełda."
    ),
    "Session": "Sesja",
    "g_session": (
        "Jeden dzień notowań. 5 sesji to ok. tydzień, 20 sesji ok. miesiąc, 60 sesji ok. "
        "trzy miesiące. Każdy typ sygnału ma swój horyzont: okres, w którym jego wynik był "
        "najwyraźniejszy."
    ),
    "Day 0 and entry": "Dzień 0 i wejście",
    "g_day0": (
        "Dzień 0 to pierwsze zamknięcie sesji po publikacji raportu. Wejście to zamknięcie "
        "sesji po wieczornym podsumowaniu, czyli pierwsza cena, po której czytelnik mógł "
        "kupić. Ruch przed wejściem jest na wykresie, ale nie w statystykach."
    ),
    "Trading costs": "Koszty transakcji",
    "g_costs": (
        "Kupno i sprzedaż kosztują: prowizja i różnica między ceną kupna a sprzedaży. Liczymy "
        "2,0% na GPW i 5,2% na NewConnect za całą transakcję. Ruch mniejszy niż te koszty "
        "nie daje zarobić."
    ),
    "Traded on new data (walk-forward)": "Handel na nowych danych (walk-forward)",
    "g_wf": (
        "Przed każdym rokiem wybieramy regułę (kupić czy sprzedać, na ile sesji) tylko z "
        "lat wcześniejszych i stosujemy ją w tym roku, po kosztach. To uczciwszy test niż "
        "średnia z całej historii, bo reguła nie zna wyników, na których jest sprawdzana."
    ),
    "Chance it is luck (FDR q)": "Szansa, że to przypadek (q po korekcie FDR)",
    "g_q": (
        "Przy kilkudziesięciu typach sygnałów i czterech horyzontach część wyników wyjdzie "
        "„istotna” przypadkiem. q uwzględnia tę liczbę prób. Poniżej 0,05 uznajemy, że ruch "
        "kursu po raportach danego typu nie jest przypadkiem."
    ),
    "Verdicts": "Werdykty",
    "g_verdicts": (
        "Działa po kosztach: ruch jest prawdziwy i handel na nowych danych zarabiał. "
        "Niepotwierdzony: zarabiał, ale za mało, by to odróżnić od przypadku. Zjadany przez "
        "koszty: ruch jest prawdziwy, ale mniejszy niż koszty. Jeszcze nie przetestowany: "
        "ruch jest prawdziwy, ale historii jest za mało na test. Szum: ruchu nie da się "
        "odróżnić od przypadku."
    ),
    "Bad case (1 in 20)": "Zły scenariusz (1 na 20)",
    "g_tail": (
        "Wynik, od którego gorzej wypadło tylko 1 na 20 wcześniejszych zdarzeń (dla reguły "
        "krótkiej sprzedaży: wzrost kursu). Nie najgorszy przypadek, bo ten opisuje jedną "
        "spółkę, a nie typ zdarzenia."
    ),
    "g_liquidity": (
        "Spółka, której mediana kursu przez 60 sesji przed raportem była poniżej 0,50 zł albo "
        "mediana dziennego obrotu poniżej 10 tys. zł. Przy takim kursie jeden tick to ruch o "
        "kilka procent, a zwykłe zlecenie to cały dzienny obrót. Takie spółki są pominięte w "
        "statystykach, a ich raporty mają oznaczenie."
    ),
    "Adjusted prices": "Kursy skorygowane",
    "g_adjusted": (
        "Kursy poprawione o podziały, scalenia, dywidendy i prawa poboru na podstawie kursów "
        "odniesienia publikowanych przez giełdę, żeby takie dni nie wyglądały jak ruch rynku."
    ),
    "Links to XTB": "Linki do XTB",
    "g_xtb": (
        "Link do strony spółki w XTB pojawia się tylko wtedy, gdy strona XTB podaje ten sam "
        "kod ISIN co raport. Undercurrent nie jest powiązany z XTB i nie poleca żadnego brokera."
    ),
    # stock of the week
    "Pick": "Wybór",
    "Stock of the week · Undercurrent": "Spółka tygodnia · Undercurrent",
    "Stock of the week": "Spółka tygodnia",
    "the model’s estimate, not a promise": "szacunek modelu, nie obietnica",
    "week of {d}": "tydzień od {d}",
    "Logo from XTB’s server; it is the company’s trademark.": (
        "Logo z serwera XTB; jest znakiem towarowym spółki."
    ),
    "How the model chose it": "Jak model ją wybrał",
    "rank among liquid WIG stocks": "miejsce wśród płynnych spółek z WIG",
    "Last 12 months vs WIG": "Ostatnie 12 miesięcy wobec WIG",
    "Median daily turnover": "Mediana dziennego obrotu",
    "A year against WIG": "Rok wobec WIG",
    "Return vs WIG": "Wynik wobec WIG",
    "a price-sensitive report (hover for its kind)": (
        "raport cenotwórczy (najedź, by zobaczyć rodzaj)"
    ),
    "The top 10 this week": "Dziesiątka tego tygodnia",
    "last 60 sessions": "ostatnie 60 sesji",
    "Expected result against WIG over 20 sessions; the line is the last 60 sessions.": (
        "Oczekiwany wynik wobec WIG w ciągu 20 sesji; linia to ostatnie 60 sesji."
    ),
    "How good is the model?": "Jak dobry jest model?",
    "Ranking accuracy (IC)": "Trafność rankingu (IC)",
    "0 = no link, 0.10 = a lot · t = {t}": "0 = brak związku, 0,10 = dużo · t = {t}",
    "Top 10 vs all liquid stocks": "Dziesiątka a wszystkie płynne spółki",
    "vs {b} for all, against WIG, before costs": (
        "wobec {b} dla wszystkich; względem WIG, przed kosztami"
    ),
    "The single pick, after costs": "Pojedynczy wybór, po kosztach",
    "{w} weeks out of sample · p = {p}": "tygodnie poza próbą: {w} · p = {p}",
    "How often the result fell inside the 90% band": (
        "Jak często wynik mieścił się w przedziale 90%"
    ),
    "live: {c} over {w} weeks": "na żywo: {c} w {w} tyg.",
    "The basket this week": "Koszyk w tym tygodniu",
    "{z} σ beyond what WIG and bonds explain": "{z} σ ponad to, co tłumaczą WIG i obligacje",
    "Expected volatility, next 20 sessions": "Oczekiwana zmienność, 20 kolejnych sesji",
    "a round trip costs about {c}": "kupno i sprzedaż kosztują około {c}",
    "Forecast vs WIG, 20 sessions": "Prognoza wobec WIG, 20 sesji",
    "90% band {lo} to {hi} · reports of the last 3 months: {n} of it · "
    "rank {r} of {u} liquid WIG stocks": (
        "przedział 90%: od {lo} do {hi} · raporty z ostatnich 3 miesięcy: {n} z tego · "
        "miejsce {r} z {u} płynnych spółek z WIG"
    ),
    "the model": "model",
    "Reports like this, as evidence": "Raporty tego typu jako dowód",
    "over 20 sessions: what any report adds, shrunk by its evidence; too few reports of this "
    "kind to tell it apart": (
        "w ciągu 20 sesji: to, co dodaje dowolny raport, ściągnięte do zera według dowodów; za "
        "mało raportów tego typu, by go odróżnić"
    ),
    "over 20 sessions after shrinking by the evidence: {c} common to all reports, {k} for this "
    "kind (measured {m} over {n} reports)": (
        "w ciągu 20 sesji po ściągnięciu według dowodów: {c} wspólne dla wszystkich raportów, "
        "{k} dla tego typu (zmierzone {m} na {n} raportach)"
    ),
    "none: the model scores only liquid WIG stocks with a year of history": (
        "brak: model ocenia tylko płynne spółki z WIG z rocznym notowaniem"
    ),
    "Up to {k} stocks whose expected result beats their own trading cost, weighted so that "
    "groups of stocks that move together carry similar risk. The rest, {wig}, stays in WIG.": (
        "Do {k} spółek, których oczekiwany wynik przewyższa ich własny koszt transakcji, z "
        "wagami dobranymi tak, by grupy spółek poruszających się razem niosły podobne ryzyko. "
        "Reszta, {wig}, zostaje w WIG."
    ),
    "Weights of the basket; the figure beside each name is its expected result against WIG "
    "over 20 sessions, before costs.": (
        "Wagi koszyka; liczba przy nazwie to oczekiwany wynik wobec WIG w ciągu 20 sesji, "
        "przed kosztami."
    ),
    "The basket, after costs": "Koszyk, po kosztach",
    "{w} weeks out of sample · p = {p} · {n} names on average": (
        "tygodnie poza próbą: {w} · p = {p} · średnio spółek: {n}"
    ),
    "live: {r} over {w} weeks": "na żywo: {r} w {w} tyg.",
    "out of sample; no live week scored yet": "poza próbą; na żywo jeszcze żaden tydzień",
    "switch": "zamiana",
    "Every feature and its weight": "Wszystkie cechy i ich wagi",
    "pick_lede": (
        "Model prognozuje wynik każdej płynnej spółki z WIG wobec WIG w ciągu 20 sesji. Raz "
        "w tygodniu do koszyka trafia do 10 spółek, których prognoza przewyższa ich własny "
        "koszt transakcji; spółka tygodnia to jego największa pozycja. Poniżej wszystko, na "
        "czym to się opiera, i jak koszyk oraz pojedynczy wybór wypadały na danych, których "
        "model nie widział."
    ),
    "The model so far": "Model do tej pory",
    "Held since {d}": "Trzymana od {d}",
    "Not yet in the live record: the live basket is recorded on the first session of a week.": (
        "Jeszcze poza zapisem na żywo: koszyk na żywo zapisuje się na pierwszej sesji tygodnia."
    ),
    "The basket’s largest position this week: {w} of the basket. Weights follow risk, so "
    "steadier stocks get more; it is not necessarily the highest forecast.": (
        "Największa pozycja koszyka w tym tygodniu: {w} koszyka. Wagi idą za ryzykiem, więc "
        "spokojniejsze spółki dostają więcej; to niekoniecznie najwyższa prognoza."
    ),
    "No stock beats its own trading cost this week, so the basket stays in WIG. This is the "
    "model’s top score.": (
        "W tym tygodniu żadna spółka nie przewyższa własnego kosztu transakcji, więc koszyk "
        "zostaje w WIG. To najwyżej oceniona spółka modelu."
    ),
    "{n} names": "spółek: {n}",
    "No live baskets yet.": "Jeszcze nie ma koszyków na żywo.",
    "Expected vs WIG, 20 sessions": "Oczekiwany wynik wobec WIG, 20 sesji",
    "Why this stock": "Dlaczego ta spółka",
    "why_note": (
        "Największe składniki oceny: każda cecha jest porównana z pozostałymi płynnymi "
        "spółkami, a jej waga pochodzi z modelu."
    ),
    "raises the score": "podnosi ocenę",
    "higher than {p}% of liquid stocks": "wyżej niż {p}% płynnych spółek",
    "lowers the score": "obniża ocenę",
    "The stock": "Spółka",
    "Share price against WIG, last year": "Kurs akcji wobec WIG, ostatni rok",
    "Return": "Wynik",
    "vs WIG": "wobec WIG",
    "1 month": "1 miesiąc",
    "3 months": "3 miesiące",
    "6 months": "6 miesięcy",
    "12 months": "12 miesięcy",
    "Bad case for 20 sessions (1 in 20)": "Zły scenariusz na 20 sesji (1 na 20)",
    "Its reports, last 90 days": "Jej raporty z ostatnich 90 dni",
    "No reports in the last 90 days.": "Brak raportów w ostatnich 90 dniach.",
    "Runners-up": "Kolejne w rankingu",
    "Rank": "Miejsce",
    "Expected vs WIG": "Oczekiwany wynik wobec WIG",
    "The full model": "Cały model",
    "model_note": (
        "Regresja grzbietowa (ridge) rang cech na rangę wyniku wobec WIG w kolejnych 20 "
        "sesjach. Trenowana na tygodniach od {start} do {end} ({n}), kara {p}. Kolumna "
        "„trafność poza próbą” to średnia korelacja rang cechy z późniejszym wynikiem w "
        "tygodniach testu."
    ),
    "Feature": "Cecha",
    "Weight": "Waga",
    "Out-of-sample rank IC": "Trafność poza próbą (IC)",
    "Out-of-sample result against WIG": "Wynik poza próbą wobec WIG",
    "The pick, after costs": "Wybór modelu, po kosztach",
    "Top 10 scores, before costs": "10 najwyżej ocenionych, przed kosztami",
    "All liquid stocks": "Wszystkie płynne spółki",
    "backtest_note": (
        "Każdy rok liczony modelem trenowanym tylko na wcześniejszych latach. Tygodnie "
        "testu: {weeks} ({start} – {end}). Wybór modelu: średnio {mean} tygodniowo po "
        "kosztach (p = {p}), liczba zamian: {switches}. Trafność rankingu (IC): {ic} (t = {t}). "
        "Sam trend 12-miesięczny tą samą regułą: {mom} łącznie."
    ),
    "Live record": "Zapis na żywo",
    "live_note": (
        "Koszyki zapisane w chwili ich złożenia, nigdy nie poprawiane; obok każdego tygodnia "
        "jego największa pozycja. Tylko ten wynik naprawdę się liczy."
    ),
    "Week": "Tydzień",
    "Switch": "Zamiana",
    "Result vs WIG, after costs": "Wynik wobec WIG, po kosztach",
    "No live picks yet.": "Jeszcze nie ma wyborów na żywo.",
    "Limitations": "Ograniczenia",
    "pick_limits": (
        "Brak danych finansowych spółek (zyski, wartość rynkowa, wskaźniki): model widzi tylko "
        "kursy, obroty i raporty bieżące. Historia w bazie ma luki, a model uczy się tylko "
        "na ciągłych okresach. Koszt każdej zamiany to 2% (cały obrót: kupno i sprzedaż). "
        "Jedna spółka to dużo szumu: ranking jako całość jest bardziej wiarygodny niż "
        "pojedynczy wybór."
    ),
    "pick_not_ready": (
        "Za mało ciągłej historii notowań do dziś, by ocenić spółki. Wybór pojawi się, gdy "
        "baza będzie miała rok ciągłych notowań kończących się dzisiejszą sesją."
    ),
    "Stock of the week: {t}": "Spółka tygodnia: {t}",
    "the model: {v}": "model: {v}",
    "See the full analysis": "Zobacz pełną analizę",
    # glossary additions
    "Rank IC": "Trafność rankingu (IC)",
    "g_ic": (
        "Korelacja rang: czy spółki ocenione wyżej rzeczywiście wypadały potem lepiej niż "
        "ocenione niżej. 0 to brak związku, 1 to idealny ranking. Na giełdzie 0,05 już coś "
        "znaczy, a 0,10 to dużo."
    ),
    "Ridge regression": "Regresja grzbietowa (ridge)",
    "g_ridge": (
        "Model liniowy z karą za duże wagi: przy wielu podobnych cechach nie pozwala żadnej "
        "zdominować wyniku. Ocena spółki to suma cech razy wagi, więc da się ją rozłożyć na "
        "części."
    ),
    "Switching rule": "Reguła zamiany",
    "g_hysteresis": (
        "Wybór zmienia się tylko, gdy nowy lider ma oczekiwany wynik lepszy o więcej niż koszt "
        "zamiany (2%). Bez tego model wymieniałby spółkę co tydzień i koszty zjadłyby wynik."
    ),
    "Live record vs backtest": "Zapis na żywo a test historyczny",
    "g_live": (
        "Test historyczny liczy, jak model wybierałby w przeszłości, na danych, których nie "
        "widział. Zapis na żywo to wybory zapisane w chwili ich dokonania. Test może być "
        "zbyt optymistyczny, zapis na żywo nie."
    ),
    # pipeline
    "Signal events": "Zdarzenia-sygnały",
    "{n} other reports": "inne raporty: {n}",
    "Studied events": "Zbadane zdarzenia",
    "since {d}": "od {d}",
    # page titles
    "Today · Undercurrent": "Dziś · Undercurrent",
    "Signals · Undercurrent": "Sygnały · Undercurrent",
    "Pipeline · Undercurrent": "Proces · Undercurrent",
}

EN_OVERRIDES: dict[str, str] = {
    "archive_lede": (
        "Events from earlier digests, newest first, with how each has done against WIG since "
        "entry. The statistics on the event pages are today's, not those of the day the "
        "report came out."
    ),
    "running": "did not finish",
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
        "Average result of the stock against WIG, from 5 sessions before to 20 after day 0, "
        "with a 95% uncertainty band. The figures below use a different window: {h} sessions "
        "from entry (the close of the session after the digest), because only from then could "
        "a reader buy."
    ),
    "today_lede_none_hold": (
        "Today's reports of kinds that have clearly moved prices in the past. None of these "
        "kinds earned after trading costs on data not used to choose it: this tells you what "
        "usually happens to the price, not what to trade."
    ),
    "today_lede_some_hold": (
        "Today's reports of kinds that have clearly moved prices in the past. Events of kinds "
        "that also earned after costs on new data ({k}) come first."
    ),
    "today_lede_empty": (
        "No report today is of a kind that has clearly moved prices in the past. All reports "
        "are listed alongside."
    ),
    "thin_title": (
        "Median price below 0.50 zł or median turnover below 10,000 zł a day over the 60 "
        "sessions before the report. Such stocks are left out of the statistics."
    ),
    "thin_warning": (
        "This stock is too cheap or too rarely traded (median price below 0.50 zł or "
        "turnover below 10,000 zł a day). Such stocks are left out of the statistics, so the "
        "figures above do not apply to it."
    ),
    "xtb_note": (
        "The instrument's page on XTB, checked against the ISIN; the logo comes from XTB's "
        "server and is the company's trademark. Undercurrent is not affiliated with XTB."
    ),
    "wf_title": (
        "A rule chosen from earlier years only, tested on the next year, after the cost of "
        "buying and selling. Average result of one trade."
    ),
    "q_title": (
        "FDR-adjusted q: how easily a result like this would come up by chance, given how "
        "many signal types are tested. Below 0.05 we treat the move as real."
    ),
    "tail_title": ("Only 1 in 20 past events did worse (for a short: the price rose more)."),
    "record_note": (
        "Events in this archive, from the close of the session after the digest until today "
        "(at most as many sessions as their kind's horizon), against WIG. One event per "
        "company, kind and day. Most windows are still open and the sample is small: an "
        "early read, not a result."
    ),
    "glossary_lede": (
        "Every figure on this site answers one question: what happened to a company's share "
        "price after reports like this in the past, compared with the whole market."
    ),
    "g_excess": (
        "The stock's return minus the WIG index's over the same time (both compounded). "
        "“4.7 pp worse than WIG” means: when WIG rose 2%, the stock fell 2.7%. "
        "Subtracting the market shows what the report did, not the whole exchange."
    ),
    "g_session": (
        "One trading day. 5 sessions is about a week, 20 about a month, 60 about three "
        "months. Each signal type has its horizon: the period in which its result was "
        "clearest."
    ),
    "g_day0": (
        "Day 0 is the first session close after the report was published. Entry is the "
        "close of the session after the evening digest: the first price a reader could have "
        "bought at. The move before entry is on the chart but not in the statistics."
    ),
    "g_costs": (
        "Buying and selling cost money: commission and the gap between bid and ask. We charge "
        "2.0% on GPW and 5.2% on NewConnect per round trip. A move smaller than that earns "
        "nothing."
    ),
    "g_wf": (
        "Before each year a rule (buy or sell, for how many sessions) is chosen from earlier "
        "years only and applied in that year, after costs. A fairer test than the average over "
        "all history, because the rule never sees the results it is tested on."
    ),
    "g_q": (
        "With dozens of signal types and four horizons some results look “significant” "
        "by chance. q accounts for the number of tries. Below 0.05 we treat the move after "
        "that kind of report as not a coincidence."
    ),
    "g_verdicts": (
        "Holds up: the move is real and trading it on new data earned. Not confirmed: it "
        "earned, but too little to tell from luck. Eaten by costs: the move is real but "
        "smaller than the costs. Not yet tested: the move is real but there is too little "
        "history to test it. Noise: the move cannot be told from chance."
    ),
    "g_tail": (
        "The result only 1 in 20 past events did worse than (for a short rule: the price "
        "rise). Not the single worst case, which describes one company, not the kind of event."
    ),
    "g_liquidity": (
        "A stock whose median price over the 60 sessions before the report was below 0.50 zł "
        "or whose median daily turnover was below 10,000 zł. At such a price one tick is a "
        "move of several percent, and an ordinary order is the whole day's market. These "
        "stocks are left out of the statistics and their reports are marked."
    ),
    "g_adjusted": (
        "Prices corrected for splits, reverse splits, dividends and rights issues using the "
        "exchange's own reference prices, so such days do not look like market moves."
    ),
    "g_xtb": (
        "A link to the company's page on XTB appears only when XTB's page shows the same ISIN "
        "as the report. Undercurrent is not affiliated with XTB and recommends no broker."
    ),
    "Avg excess [0,+{h}]": "Avg excess, {h} sessions after entry",
    "pick_lede": (
        "The model forecasts every liquid WIG stock's result against WIG over the next 20 "
        "sessions. Once a week up to 10 stocks whose forecast beats their own trading cost go "
        "into the basket; the stock of the week is its largest position. Below is everything "
        "this rests on, and how the basket and a single pick did on data the model had not "
        "seen."
    ),
    "why_note": (
        "The largest parts of the score: each feature is compared with the other liquid "
        "stocks, and its weight comes from the model."
    ),
    "model_note": (
        "Ridge regression of the features' ranks on the rank of the result against WIG over "
        "the next 20 sessions. Trained on weeks from {start} to {end} ({n}), penalty {p}. "
        "“Out-of-sample rank IC” is the feature's average rank correlation with the "
        "later result in the test weeks."
    ),
    "backtest_note": (
        "Each year scored by a model trained only on earlier years. Test weeks: {weeks} "
        "({start} – {end}). The pick: {mean} a week on average after costs (p = {p}), "
        "switches: {switches}. Ranking accuracy (IC): {ic} (t = {t}). The 12-month trend alone, "
        "same rule: {mom} in total."
    ),
    "live_note": (
        "Baskets recorded when they were made, never corrected; beside each week, its largest "
        "position. Only this result really counts."
    ),
    "pick_limits": (
        "No company financials (earnings, market value, ratios): the model sees only prices, "
        "turnover and current reports. The lake's history has holes, and the model learns "
        "only from continuous stretches. Every switch costs 2% (a full round trip). One stock "
        "is a lot of noise: the ranking as a whole is more reliable than a single pick."
    ),
    "pick_not_ready": (
        "Not enough continuous price history up to today to score stocks. The pick appears "
        "once the lake has a year of continuous sessions ending today."
    ),
    "g_ic": (
        "Rank correlation: whether stocks scored higher really did better afterwards than "
        "those scored lower. 0 is no link, 1 a perfect ranking. On a stock market 0.05 already "
        "means something, and 0.10 is a lot."
    ),
    "g_ridge": (
        "A linear model with a penalty on large weights: with many similar features it lets "
        "none of them dominate. A stock's score is the sum of features times weights, so it "
        "can be taken apart."
    ),
    "g_hysteresis": (
        "The pick changes only when the new leader's expected result is better by more than "
        "the cost of switching (2%). Without it the model would swap every week and costs "
        "would eat the result."
    ),
    "g_live": (
        "The backtest computes how the model would have picked in the past, on data it had "
        "not seen. The live record is the picks as they were made. A backtest can flatter; "
        "the live record cannot."
    ),
}

# Polish plural forms: (1, 2–4, 5+), keyed by the English singular.
PL_PLURALS: dict[str, tuple[str, str, str]] = {
    "event of a kind that has moved prices": (
        "zdarzenie z typu, który ruszał kursem",
        "zdarzenia z typów, które ruszały kursem",
        "zdarzeń z typów, które ruszały kursem",
    ),
    "event worth a look today": (
        "zdarzenie warte uwagi dziś",
        "zdarzenia warte uwagi dziś",
        "zdarzeń wartych uwagi dziś",
    ),
    "session": ("sesja", "sesje", "sesji"),
}
EN_PLURALS: dict[str, str] = {
    "event of a kind that has moved prices": "events of kinds that have moved prices",
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
# One sentence per verdict, saying what it means for a reader.
VERDICT_EXPLAIN: dict[str, dict[str, str]] = {
    "en": {
        "holds_up": (
            "A rule chosen from earlier years only also earned after trading costs in the "
            "years after."
        ),
        "unconfirmed": ("After costs the result was positive, but too weak to tell from luck."),
        "eaten_by_costs": (
            "The price move is real, but smaller than the cost of buying and selling: "
            "trading on it lost money."
        ),
        "untested": (
            "The price move is real, but there is too little history yet to test trading it "
            "on new data."
        ),
        "noise": "The price move after such reports cannot be told from chance.",
        "too_few": "Too few past events to say anything.",
    },
    "pl": {
        "holds_up": (
            "Reguła wybrana tylko z wcześniejszych lat zarabiała także po kosztach transakcji "
            "w kolejnych latach."
        ),
        "unconfirmed": (
            "Po kosztach wynik był dodatni, ale za słaby, by odróżnić go od przypadku."
        ),
        "eaten_by_costs": (
            "Ruch kursu jest prawdziwy, ale mniejszy niż koszt kupna i sprzedaży: handel na "
            "nim tracił."
        ),
        "untested": (
            "Ruch kursu jest prawdziwy, ale historii jest jeszcze za mało, by sprawdzić "
            "handel na nowych danych."
        ),
        "noise": "Ruchu kursu po takich raportach nie da się odróżnić od przypadku.",
        "too_few": "Za mało wcześniejszych zdarzeń, by cokolwiek powiedzieć.",
    },
}
# Stock of the week: feature names, verdicts and what they mean.
FEATURE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "mom_12_1": "12-month trend vs WIG (last month left out)",
        "mom_6_1": "6-month trend vs WIG (last month left out)",
        "rev_1m": "Last month vs WIG",
        "off_high": "Distance from the 52-week high",
        "vol_60": "Volatility (3 months, annualised)",
        "beta": "Beta to WIG (1 year)",
        "resid_vol": "Own volatility beyond WIG's (annualised)",
        "drawdown": "Deepest fall in the last year",
        "turnover": "Median daily turnover",
        "no_trade": "Sessions without a trade",
        "illiquidity": "Price impact of trading (Amihud)",
        "news_insider_buy": "Insider purchases reported (3 months)",
        "news_insider_sell": "Insider sales reported (3 months)",
        "news_holder": "Holder thresholds crossed (3 months)",
        "news_profit_warning": "Results estimates / warnings (3 months)",
        "news_contract": "Large contracts (3 months)",
        "news_financing": "Financing reports (3 months)",
        "news_buyback": "Buybacks and dividends (3 months)",
        "news_m_and_a": "M&A and tender offers (3 months)",
        "news_other": "Other price-sensitive reports (3 months)",
        "insider_net": "Insider net buying, last month (days of turnover)",
        "corp_actions": "Corporate actions (3 months)",
        "garch_vol": "Expected volatility, next month (GARCH)",
        "kalman_beta": "Beta to WIG, current (Kalman)",
        "bond_beta": "Sensitivity to bond prices (TBSP)",
        "resid_mom": "12-month trend of its own (beyond WIG and bonds)",
        "surprise_20": "Surprises last month (in standard deviations)",
        "signal_effect": "What its recent reports are expected to add (shrunk event study)",
    },
    "pl": {
        "mom_12_1": "Trend 12 miesięcy wobec WIG (bez ostatniego miesiąca)",
        "mom_6_1": "Trend 6 miesięcy wobec WIG (bez ostatniego miesiąca)",
        "rev_1m": "Ostatni miesiąc wobec WIG",
        "off_high": "Odległość od rocznego maksimum",
        "vol_60": "Zmienność (3 miesiące, w skali roku)",
        "beta": "Beta wobec WIG (rok)",
        "resid_vol": "Własna zmienność ponad WIG (w skali roku)",
        "drawdown": "Największy spadek w ostatnim roku",
        "turnover": "Mediana dziennego obrotu",
        "no_trade": "Sesje bez transakcji",
        "illiquidity": "Wpływ handlu na cenę (Amihud)",
        "news_insider_buy": "Zgłoszone zakupy insiderów (3 miesiące)",
        "news_insider_sell": "Zgłoszone sprzedaże insiderów (3 miesiące)",
        "news_holder": "Przekroczone progi akcjonariatu (3 miesiące)",
        "news_profit_warning": "Szacunki wyników / ostrzeżenia (3 miesiące)",
        "news_contract": "Znaczące umowy (3 miesiące)",
        "news_financing": "Raporty o finansowaniu (3 miesiące)",
        "news_buyback": "Skupy akcji i dywidendy (3 miesiące)",
        "news_m_and_a": "Fuzje, przejęcia, wezwania (3 miesiące)",
        "news_other": "Inne informacje poufne (3 miesiące)",
        "insider_net": "Saldo zakupów insiderów, ostatni miesiąc (w dniach obrotu)",
        "corp_actions": "Operacje na akcjach (3 miesiące)",
        "garch_vol": "Oczekiwana zmienność w przyszłym miesiącu (GARCH)",
        "kalman_beta": "Beta wobec WIG, bieżąca (Kalman)",
        "bond_beta": "Wrażliwość na ceny obligacji (TBSP)",
        "resid_mom": "Własny trend 12 miesięcy (poza WIG i obligacjami)",
        "surprise_20": "Zaskoczenia w ostatnim miesiącu (w odchyleniach standardowych)",
        "signal_effect": "Oczekiwany wpływ ostatnich raportów (badanie zdarzeń, ściągnięty do 0)",
    },
}
PICK_VERDICTS: dict[str, dict[str, str]] = {
    "en": {
        "beats_wig": "beats WIG after costs",
        "unconfirmed": "positive, not confirmed",
        "no_edge": "no edge shown",
        "too_short": "too little history to judge",
    },
    "pl": {
        "beats_wig": "pokonuje WIG po kosztach",
        "unconfirmed": "dodatni, bez potwierdzenia",
        "no_edge": "bez wykazanej przewagi",
        "too_short": "za krótka historia, by ocenić",
    },
}
PICK_EXPLAIN: dict[str, dict[str, str]] = {
    "en": {
        "beats_wig": (
            "Built week by week on data it had not seen, the model's basket beat WIG after "
            "trading costs, by more than chance explains."
        ),
        "unconfirmed": (
            "Built week by week on data it had not seen, the model's basket beat WIG after "
            "costs, but not by enough to tell it from luck."
        ),
        "no_edge": (
            "Built week by week on data it had not seen, the model's basket did not beat WIG "
            "after costs. This stock is the basket's largest position, not a proven winner."
        ),
        "too_short": (
            "The model has not yet been tested on enough weeks it had not seen to say "
            "anything about its baskets."
        ),
    },
    "pl": {
        "beats_wig": (
            "Składany co tydzień na danych, których model nie widział, koszyk modelu "
            "pokonywał WIG po kosztach transakcji, bardziej niż tłumaczy to przypadek."
        ),
        "unconfirmed": (
            "Składany co tydzień na danych, których model nie widział, koszyk modelu "
            "pokonywał WIG po kosztach, ale za słabo, by odróżnić to od przypadku."
        ),
        "no_edge": (
            "Składany co tydzień na danych, których model nie widział, koszyk modelu nie "
            "pokonywał WIG po kosztach. Ta spółka to największa pozycja koszyka, a nie "
            "sprawdzony zwycięzca."
        ),
        "too_short": (
            "Model nie był jeszcze sprawdzony na dość wielu tygodniach, których nie widział, "
            "by cokolwiek powiedzieć o jego koszykach."
        ),
    },
}
# Section titles on the Today page, one per verdict.
TIER_TITLES: dict[str, dict[str, str]] = {
    "en": {
        "holds_up": "Earned after costs",
        "unconfirmed": "Earned after costs, not confirmed",
        "eaten_by_costs": "Moved the price, costs ate the gain",
        "untested": "Moved the price, not yet tested",
    },
    "pl": {
        "holds_up": "Zarabiały po kosztach",
        "unconfirmed": "Zarabiały po kosztach, bez potwierdzenia",
        "eaten_by_costs": "Ruszały kursem, koszty zjadały zysk",
        "untested": "Ruszały kursem, jeszcze nie sprawdzone",
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
    # anything that is not a number shows as "—" (a template's missing value included)
    return not isinstance(x, int | float) or (isinstance(x, float) and math.isnan(x))


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
