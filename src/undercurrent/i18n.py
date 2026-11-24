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
    "Newest first. Every report shows its type; a price-sensitive one also shows how reports "
    "of its type have moved prices before.": (
        "Najnowsze na górze. Każdy raport ma swój typ; przy raporcie cenotwórczym widać też, "
        "jak raporty tego typu ruszały wcześniej kursem."
    ),
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
    "The logo is the company’s trademark, shown only to identify the stock.": (
        "Logo jest znakiem towarowym spółki i służy tylko do jej rozpoznania."
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
        "w tygodniu koszyk kupuje 15 spółek z najlepszą prognozą i trzyma każdą, dopóki nie "
        "wypadnie z najlepszych 45; spółka tygodnia to ta z najlepszą prognozą. Poniżej "
        "wszystko, na czym to się opiera, i jak koszyk oraz pojedynczy wybór wypadały na "
        "danych, których model nie widział."
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
        "spółka z najlepszą prognozą. Tylko ten wynik naprawdę się liczy."
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
    # analysis pages: market, screener, stock
    "Phase changes this week": "Zmiany fazy w tym tygodniu",
    "No sector changed phase this week.": "Żaden sektor nie zmienił fazy w tym tygodniu.",
    "pitch_h1": "Giełda po sesji, w jednym miejscu.",
    "pitch_sub": (
        "Wykresy, skaner i wieczorne raporty ESPI wszystkich spółek GPW i NewConnect. "
        "Codziennie po sesji, za darmo."
    ),
    "pitch_meta": (
        "Wykresy, skaner i wieczorne raporty ESPI wszystkich spółek GPW i NewConnect, "
        "codziennie po sesji. Bez rekomendacji, same dane."
    ),
    "Open the screener": "Otwórz skaner",
    "Tonight’s reports": "Raporty dziś",
    "listed stocks, each with a chart": "notowanych spółek, każda z wykresem",
    "ESPI/EBI reports tonight": "raportów ESPI/EBI dziś",
    "price history since": "historia kursów od",
    "Tradable stocks only.": "Tylko spółki, którymi da się handlować.",
    "rrg_caption": "Sektory po prawej biją WIG; u góry ich przewaga rośnie.",
    "screener_lede_short": "Wszystkie notowane spółki. Sortuj, filtruj albo wybierz gotowy zestaw.",
    "reports_lede_short": "Wszystkie raporty ESPI i EBI z dzisiejszego wieczoru, od najnowszego.",
    "Big moves today": "Duże ruchy dziś",
    "movers_lede": (
        "Spółki, które dziś poszły ponad ±2σ względem rynku, i wszystko, co o nich "
        "opublikowano od poprzedniego zamknięcia: raporty i nagłówki, od najnowszego. To nie "
        "jest wyjaśnienie ruchu."
    ),
    "after the close": "po zamknięciu",
    "routine notice": "raport rutynowy",
    "Nothing was published about the company since the previous close.": (
        "Od poprzedniego zamknięcia nic o spółce nie opublikowano."
    ),
    "signals_lede_short": (
        "Które rodzaje raportów ruszają kursem w dniu publikacji, a które jeszcze zarabiają "
        "po podsumowaniu, po kosztach."
    ),
    "Do reports move the price on the day?": "Czy raporty ruszają kursem w dniu publikacji?",
    "reaction_note": (
        "Ruch kursu wobec rynku od dnia raportu do sesji, na której czytelnik podsumowania może "
        "pierwszy raz kupić, w jednostkach zwykłej zmienności spółki, porównany z dniami bez "
        "żadnego raportu. Ruch bywa w górę i w dół, więc średnia wynosi około zera; liczy się "
        "jego wielkość. Tabela niżej mierzy co innego: czy po podsumowaniu da się jeszcze "
        "zarobić po kosztach."
    ),
    "Kind of report": "Rodzaj raportu",
    "Size of the day’s move, ordinary day = 1": "Wielkość ruchu, zwykły dzień = 1",
    "Moves beyond ±2σ": "Ruchy powyżej ±2σ",
    "Typical move": "Typowy ruch",
    "ordinary {x}": "zwykle {x}",
    "Moves the price": "Rusza kursem",
    "No clear move": "Brak wyraźnego ruchu",
    "Too few reports": "Za mało raportów",
    "Reports of this kind move the price {x}× as much as an ordinary day.": (
        "Raporty tego rodzaju ruszają kursem {x}× mocniej niż zwykły dzień."
    ),
    "Today’s move against the market, in the stock’s own σ: a big move is beyond ±2σ.": (
        "Dzisiejszy ruch wobec rynku, w jednostkach zmienności spółki: duży ruch to ponad ±2σ."
    ),
    "setups_lede_short": "Każdy zestaw ze skanera, sprawdzony na historii GPW.",
    "archive_lede_short": "Wcześniejsze podsumowania i jak ich spółki radzą sobie od tamtej pory.",
    "record_note_short": "Wynik względem WIG od wejścia do dziś.",
    "glossary_lede_short": (
        "Krótkie definicje pojęć ze strony. Najedź na podkreślony termin, by zobaczyć go w miejscu."
    ),
    "how_lede": "Jak liczymy to, co pokazuje strona: źródła, metody i ograniczenia.",
    "At a glance": "W skrócie",
    "Trend": "Trend",
    "above the 200-session average": "powyżej średniej z 200 sesji",
    "below the 200-session average": "poniżej średniej z 200 sesji",
    "From the 52-week high": "Od szczytu 52 tygodni",
    "Typical daily range": "Typowy zakres dnia",
    "Forecast vs WIG, 20 s.": "Prognoza wzgl. WIG, 1 mies.",
    "Insiders, 20 s.": "Insiderzy, 1 mies.",
    "σ band": "Pasmo σ",
    "Click a dot to open its report.": "Kliknij kropkę, aby otworzyć raport.",
    "only for liquid WIG stocks": "tylko dla płynnych spółek z WIG",
    "xtb_short": "Undercurrent nie jest powiązany z XTB.",
    "How it’s measured": "Jak to liczymy",
    "Best forecast in this week’s basket": "Najlepsza prognoza w koszyku na ten tydzień",
    "Top score this week. There is no basket this week.": (
        "Najwyższa ocena modelu w tym tygodniu. W tym tygodniu nie ma koszyka."
    ),
    "vs all liquid stocks: {a}": "wobec wszystkich płynnych spółek: {a}",
    "{r} vs WIG after costs": "{r} wzgl. WIG po kosztach",
    "Not in the live record yet.": "Jeszcze nie w zapisie na żywo.",
    "{w} weeks on unseen data": "{w} tyg. na nowych danych",
    "{k} stocks in equal parts. A stock stays until it drops out of the best {m}.": (
        "{k} spółek po równo. Spółka zostaje, dopóki nie wypadnie z najlepszych {m}."
    ),
    "Weight in the basket; beside each name, the expected result vs WIG over 20 sessions.": (
        "Waga w koszyku; obok spółki jej oczekiwany wynik wzgl. WIG w ciągu 20 sesji."
    ),
    "Saved each week as it was made, never edited.": (
        "Zapisywany co tydzień w chwili wyboru, nigdy nie poprawiany."
    ),
    "How the model works and its limits": "Jak działa model i jakie ma ograniczenia",
    "not counted in these statistics.": "nie wchodzi do tych statystyk.",
    "Source: the MAR notification form": "Źródło: formularz powiadomienia MAR",
    "Source: the report’s text": "Źródło: treść raportu",
    "surprise {z}σ": "niespodzianka {z}σ",
    "not yet": "jeszcze nie",
    "90% band {lo} to {hi}": "pasmo 90% od {lo} do {hi}",
    "rank {r} of {u}": "miejsce {r} z {u}",
    "over 20 sessions": "w ciągu 20 sesji",
    "Average vs WIG around day 0, with a 95% band.": (
        "Średni wynik wzgl. WIG wokół dnia 0, z pasmem 95%."
    ),
    "No stock found": "Nie znaleziono spółki",
    "All results in the screener": "Wszystkie wyniki w skanerze",
    "How it works": "Jak to działa",
    "How it works · Undercurrent": "Jak to działa · Undercurrent",
    "Behind the numbers": "Za liczbami",
    "On this page": "Na tej stronie",
    "The market page": "Strona rynku",
    "Reports and signals": "Raporty i sygnały",
    "Stock pages": "Strony spółek",
    "Data and links": "Dane i linki",
    "Biggest moves and surprises": "Największe ruchy i niespodzianki",
    "How a kind of report is tested": "Jak sprawdzamy rodzaj raportu",
    "How a setup is tested": "Jak sprawdzamy układ",
    "Insider trade details": "Szczegóły transakcji insiderów",
    "The archive’s record": "Wyniki archiwum",
    "How the model chooses": "Jak model wybiera",
    "Tested on weeks it had not seen": "Sprawdzony na tygodniach, których nie widział",
    "Company logos": "Logo spółek",
    "Terms from A to Z": "Pojęcia od A do Z",
    "event_chart_note_plain": (
        "Wykres zdarzenia pokazuje średni wynik akcji względem WIG od 5 sesji przed do 20 "
        "sesji po dniu 0, z 95% pasmem niepewności. Liczby pod nim dotyczą okresu od wejścia "
        "(zamknięcia sesji po podsumowaniu), bo dopiero wtedy czytelnik mógł kupić."
    ),
    "forms_how": (
        "Gdy formularz powiadomienia MAR jest dostępny w kopii na parkiet.com, bankier.pl "
        "albo biznesradar.pl, pola pochodzą z niego, a czego nie podaje, uzupełnia treść "
        "raportu."
    ),
    "logos_how": (
        "Logo to logo XTB dla kodu giełdowego spółki albo ikona z jej własnej strony, "
        "przechowywane i serwowane przez Undercurrent. Jest znakiem towarowym spółki i służy "
        "tylko do jej rozpoznania."
    ),
    "Pages of reports": "Strony raportów",
    "Previous": "Poprzednia",
    "Next": "Następna",
    "Page {p} of {n}": "Strona {p} z {n}",
    "{a}–{b} of {n}": "{a}–{b} z {n}",
    "Valuation": "Wycena",
    "Market cap": "Kapitalizacja",
    "Measure": "Miara",
    "Percentile": "Percentyl",
    "against the sector": "na tle sektora",
    "against the sector group": "na tle grupy sektorów",
    "against all listed stocks": "na tle wszystkich notowanych spółek",
    "Earnings yield": "Stopa zysku",
    "Book to price": "Wartość księgowa do ceny",
    "Free cash flow yield": "Stopa wolnych przepływów",
    "EBIT yield": "Stopa EBIT",
    "Dividend yield": "Stopa dywidendy",
    "Value score": "Wynik wartości",
    "Value rank": "Ranking wartości",
    "needs an annual report from the last 18 months": (
        "wymaga raportu rocznego z ostatnich 18 miesięcy"
    ),
    "no_recent_report": (
        "Brak raportu rocznego z ostatnich 18 miesięcy w danych: repozytorium ESEF, z którego "
        "pochodzą, nie ma polskich raportów dodanych po lipcu 2024. Liczba akcji, zysk i "
        "wartość księgowa pochodzą z dzisiejszej tabeli GPW."
    ),
    "Fiscal year ending {d}: older than 18 months, shown for reference only.": (
        "Rok obrotowy kończący się {d}: starszy niż 18 miesięcy, tylko dla orientacji."
    ),
    "val_note": (
        "Stopy zamiast mnożników: strata to niska stopa, a nie ujemny C/Z. Percentyl 100 to "
        "najtańsza spółka w grupie. Wynik wartości to średni percentyl stopy zysku, wartości "
        "księgowej do ceny i stopy wolnych przepływów. Bez EV/EBITDA: zadłużenie bankowe nie "
        "jest jednolicie oznaczane w raportach ESEF."
    ),
    "What the price implies": "Co zakłada cena",
    "discount rate {r}": "stopa dyskontowa {r}",
    "above {g} a year": "ponad {g} rocznie",
    "below {g} a year": "poniżej {g} rocznie",
    "a year": "rocznie",
    "dcf_note": (
        "Odwrócony DCF: roczny wzrost wolnych przepływów przez 10 lat (potem 2,5% rocznie), "
        "przy którym ich wartość bieżąca równa się kapitalizacji. Na poziomie kapitału "
        "własnego, bez korekty o dług. To założenie rynku, nie prognoza."
    ),
    (
        "No positive free cash flow in the last annual report: "
        "there is no growth the price could imply."
    ): (
        "W ostatnim raporcie rocznym wolne przepływy nie są dodatnie: cena nie zakłada "
        "żadnego wzrostu, który dałoby się policzyć."
    ),
    "Quality of the last annual report": "Jakość ostatniego raportu rocznego",
    "tests the report allows": "testów, na które pozwala raport",
    "No fresh annual report in the data.": "Brak świeżego raportu rocznego w danych.",
    "Altman Z": "Z Altmana",
    "not for financial companies": "nie dla spółek finansowych",
    "a line it needs is not in the report": "w raporcie brakuje potrzebnej pozycji",
    "Shares and book value: GPW indicators of {d}.": (
        "Liczba akcji i wartość księgowa: wskaźniki GPW z dnia {d}."
    ),
    "Shares from the annual report (profit ÷ earnings per share).": (
        "Liczba akcji z raportu rocznego (zysk ÷ zysk na akcję)."
    ),
    "Annual report: fiscal year ending {d}.": "Raport roczny: rok obrotowy kończący się {d}.",
    "No fresh annual report: cash flow, EBIT and quality are missing.": (
        "Brak świeżego raportu rocznego: bez przepływów, EBIT i oceny jakości."
    ),
    "Not investment advice.": "To nie jest rekomendacja inwestycyjna.",
    "f_roa_positive": "zwrot z aktywów dodatni",
    "f_cfo_positive": "przepływy operacyjne dodatnie",
    "f_roa_up": "zwrot z aktywów wzrósł",
    "f_cash_above_profit": "przepływy wyższe niż zysk",
    "f_leverage_down": "zobowiązania długoterminowe do aktywów spadły",
    "f_liquidity_up": "płynność bieżąca wzrosła",
    "f_no_new_shares": "bez nowych akcji",
    "f_margin_up": "marża brutto wzrosła",
    "f_turnover_up": "rotacja aktywów wzrosła",
    "z_safe": "strefa bezpieczna",
    "z_grey": "strefa szara",
    "z_distress": "strefa zagrożenia",
    "Cheap against its sector": "Tania na tle sektora",
    "Strong annual report (F-score 7+)": "Mocny raport roczny (F-score 7+)",
    "Altman distress zone": "Strefa zagrożenia Altmana",
    "Div. yield": "Stopa dyw.",
    "FCF yield": "Stopa FCF",
    "t_mcap": "Liczba akcji z tabeli wskaźników GPW razy dzisiejszy kurs.",
    "t_pe": "Na dzisiejszym kursie: zysk z tabeli wskaźników GPW, wartość księgowa i dywidenda.",
    "t_fcfy": (
        "Przepływy operacyjne minus nakłady inwestycyjne z ostatniego raportu rocznego, do "
        "kapitalizacji."
    ),
    "t_value": "Średni percentyl stopy zysku, B/P i stopy FCF w sektorze; 100% to najtańsza.",
    "t_fscore": (
        "F-score Piotroskiego: ile z dziewięciu testów jakości raport zdaje (gdy da się "
        "ocenić co najmniej osiem)."
    ),
    "t_altman": (
        "Z Altmana: poniżej 1,81 strefa zagrożenia, powyżej 2,99 bezpieczna; nie dla finansów."
    ),
    "Valuation yields": "Stopy wyceny",
    "g_valuation": (
        "Kapitalizacja to liczba akcji z dziennej tabeli wskaźników GPW razy kurs. Stopa "
        "zysku (E/P), wartość księgowa do ceny (B/P), stopa wolnych przepływów (przepływy "
        "operacyjne minus nakłady na środki trwałe i wartości niematerialne, do "
        "kapitalizacji) i stopa EBIT są odwrotnościami mnożników, więc strata to po prostu "
        "niska stopa. Każda jest porównana ze spółkami tego samego sektora GPW (co najmniej "
        "sześć z wartością), inaczej z tej samej grupy sektorów, a inaczej ze wszystkimi "
        "notowanymi."
    ),
    "Reverse DCF": "Odwrócony DCF",
    "g_dcf": (
        "Zamiast zgadywać wzrost i liczyć z niego wartość, odwrócony DCF pyta, jaki roczny "
        "wzrost wolnych przepływów przez dziesięć lat, a potem 2,5% rocznie, uzasadnia "
        "dzisiejszą kapitalizację przy stopie dyskontowej 8, 10 i 12%. Wysoka liczba znaczy, "
        "że rynek oczekuje dużo."
    ),
    "Piotroski F-score": "F-score Piotroskiego",
    "g_fscore": (
        "Dziewięć prostych testów raportu rocznego wobec poprzedniego roku: zyskowność, "
        "przepływy, ich przewaga nad zyskiem, zadłużenie (tu: zobowiązania długoterminowe), "
        "płynność, emisje akcji, marża i rotacja aktywów. Pokazujemy, ile testów raport "
        "pozwala ocenić."
    ),
    "g_altman": (
        "Z Altmana łączy kapitał obrotowy, zyski zatrzymane, EBIT i przychody (każde do "
        "aktywów) oraz kapitalizację do zobowiązań. Poniżej 1,81 historycznie częściej "
        "zdarzały się bankructwa. Nie liczymy go dla banków, ubezpieczycieli i funduszy."
    ),
    "Pullback in an uptrend": "Korekta w trendzie wzrostowym",
    "Volatility squeeze": "Ściśnięcie zmienności",
    "Breakout on heavy turnover": "Wybicie przy dużym obrocie",
    "Oversold near the 52-week low": "Wyprzedanie blisko minimum 52-tygodniowego",
    "Healthy trend (ADX above 25)": "Zdrowy trend (ADX powyżej 25)",
    "Rel. turnover": "Wzgl. obrót",
    "Drawdown": "Spadek od szczytu",
    "σ percentile": "Percentyl σ",
    "t_rvol": "Dzisiejszy obrót względem średniej z 20 poprzednich sesji.",
    "t_adx": (
        "ADX Wildera z 14 sesji: siła trendu niezależnie od kierunku; powyżej 25 trend jest "
        "wyraźny."
    ),
    "t_var": (
        "Wartość zagrożona na jedną sesję: strata, której kurs nie powinien przekroczyć w "
        "95% sesji."
    ),
    "t_es": "Średnia strata w 2,5% najgorszych sesji (oczekiwany niedobór).",
    "t_dd": "Kurs względem najwyższego od ostatniej luki w danych.",
    "t_volpct": "Jaka część sesji z ostatniego roku miała σ niższą niż dziś.",
    "Risk: what a bad session looks like": "Ryzyko: jak wygląda zła sesja",
    "Value at risk, one session": "Wartość zagrożona, jedna sesja",
    "Expected shortfall (97.5%)": "Oczekiwany niedobór (97,5%)",
    "the mean loss on the worst 2.5% of sessions": "średnia strata w 2,5% najgorszych sesji",
    "Tails": "Ogony rozkładu",
    "fatter than normal the lower ν is": "tym grubsze od normalnych, im niższe ν",
    "normal: too little history to fit the tails": (
        "normalne: za krótka historia, by dopasować ogony"
    ),
    "Calm or not": "Spokój czy nie",
    "of the last year’s sessions had a lower σ than tonight": (
        "sesji z ostatniego roku miało σ niższą niż dziś"
    ),
    "Below its high": "Poniżej szczytu",
    "Deepest fall, last year": "Największy spadek, ostatni rok",
    "Backtest, last year": "Test wsteczny, ostatni rok",
    "expected about {e}": "oczekiwano około {e}",
    "consistent with the model": "zgodne z modelem",
    "more or fewer breaches than the model expects": (
        "więcej lub mniej przekroczeń, niż zakłada model"
    ),
    "not enough history yet": "jeszcze za krótka historia",
    "risk_note": (
        "Z prognozy σ na kolejną sesję (GARCH z zakresem dnia) i rozkładu t-Studenta o "
        "jednostkowej wariancji, dopasowanego do historii spółki. Test wsteczny używa σ "
        "znanej wieczór wcześniej i ν dopasowanego przed badanym rokiem. Test Kupca: p "
        "poniżej 0,05 oznacza, że liczba przekroczeń nie pasuje do modelu. To nie jest "
        "rekomendacja inwestycyjna."
    ),
    "Sector rotation": "Rotacja sektorów",
    "The GPW sector indices against WIG": "Indeksy sektorowe GPW względem WIG",
    "Sector": "Sektor",
    "Against WIG, except today: the index’s own change.": (
        "Względem WIG, z wyjątkiem dziś: zmiana samego indeksu."
    ),
    "Phase": "Faza",
    "vs WIG, 5 s.": "wzgl. WIG, 5 ses.",
    "Leading": "Liderzy",
    "Weakening": "Słabnący",
    "Lagging": "Maruderzy",
    "Improving": "Poprawiający się",
    "Relative trend": "Trend względny",
    "Momentum": "Impet",
    "Sector rotation against WIG": "Rotacja sektorów względem WIG",
    "rrg_note": (
        "W prawo: sektor ostatnio bije WIG (kurs względem WIG ponad jego średnią z 50 "
        "sesji). W górę: ta przewaga rośnie (wobec stanu sprzed 10 sesji). Ogon to ostatnie "
        "cztery tygodnie; sektory zwykle krążą zgodnie z ruchem wskazówek zegara. To opis, nie "
        "prognoza."
    ),
    "ADX and relative turnover": "ADX i obrót względny",
    "g_adx": (
        "ADX 14 (Wildera) mierzy siłę trendu bez względu na kierunek: wygładzone wzrosty "
        "maksimów i spadki minimów względem rzeczywistego zakresu. Powyżej 25 trend jest "
        "wyraźny. Obrót względny to dzisiejszy obrót wobec średniej z 20 poprzednich sesji; "
        "1,5 i więcej to wyraźnie więcej handlu niż zwykle. Szerokość wstęg Bollingera to "
        "(górna − dolna) / środkowa; najniższa od pół roku oznacza ściśnięcie zmienności."
    ),
    "Value at risk and expected shortfall": "Wartość zagrożona i oczekiwany niedobór",
    "g_var": (
        "VaR 95% to strata na jednej sesji, której kurs nie powinien przekroczyć w 95 "
        "sesjach na 100; oczekiwany niedobór 97,5% to średnia strata w 2,5% najgorszych "
        "sesji. Oba z prognozy σ na jutro i rozkładu t-Studenta przeskalowanego do "
        "jednostkowej wariancji, bo zwykły rozkład t ma większe odchylenie niż 1 i zawyżałby "
        "ryzyko. Test wsteczny porównuje liczbę przekroczeń w ostatnim roku z oczekiwaną "
        "(test Kupca)."
    ),
    "g_rotation": (
        "Indeks sektora dzielony przez WIG to siła względna. Trend to ta siła wobec jej "
        "średniej z 50 sesji, impet to zmiana trendu w 10 sesji; oba wynoszą 100, gdy nic "
        "się nie zmienia. Liderzy: oba powyżej 100; słabnący: trend powyżej, impet poniżej; "
        "maruderzy: oba poniżej; poprawiający się: trend poniżej, impet powyżej."
    ),
    "Chart setups": "Układy na wykresie",
    "Do the screener’s presets pay off?": "Czy zestawy ze skanera się opłacają?",
    "setups_lede": (
        "Każdy zestaw ze skanera sprawdzony jak raporty: zdarzeniem jest sesja, na której "
        "spółka wchodzi w dany stan (na przykład RSI spada poniżej 30). Wejście na "
        "zamknięciu następnej sesji, koszty transakcyjne, tylko spółki, którymi da się "
        "handlować, ta sama korekta na wielokrotne testy i ten sam test na nowych danych."
    ),
    "setups_footnote": (
        "Ta sama spółka liczy się ponownie dopiero po 20 sesjach. Wskaźniki na cenach "
        "skorygowanych, liczone od nowa po luce w danych. Układy są osobną rodziną testów: "
        "nie zmieniają statystyk raportów."
    ),
    "setup_line": (
        "Spółki wchodzące w ten zestaw ({n} przypadków): średnio {mean} względem WIG w ciągu 20 "
        "sesji, na nowych danych po kosztach {oos} na transakcję. Werdykt: {verdict}."
    ),
    "Chart indicators": ("Wskaźniki na wykresie"),
    "g_indicators": (
        "Liczone na cenach skorygowanych o dywidendy i splity, każdy od nowa po luce w "
        "danych. SMA n: średnia z n ostatnich kursów. RSI 14 (Wildera): średni wzrost wobec "
        "średniego spadku z 14 sesji, od 0 do 100; powyżej 70 spółka ostatnio głównie rosła, "
        "poniżej 30 głównie spadała. MACD 12, 26, 9: różnica średnich wykładniczych z 12 i "
        "26 sesji, jej średnia z 9 sesji i histogram różnicy. Bollinger 20, 2: SMA 20 ± 2 "
        "odchylenia standardowe. ATR 14: średni rzeczywisty zakres sesji. Maksimum i minimum "
        "52-tygodniowe: z 250 ostatnich sesji. To opisy przeszłości, nie prognozy."
    ),
    "g_surprise": (
        "Ruch dnia, którego nie tłumaczą WIG i obligacje (betą spółki), podzielony przez "
        "odchylenie standardowe prognozowane wieczór wcześniej. Wartość 3 lub więcej zdarza "
        "się rzadko: coś się wydarzyło."
    ),
    "g_beta": (
        "O ile procent zwykle zmienia się kurs, gdy WIG zmienia się o 1%. Liczona filtrem "
        "Kalmana, więc może zmieniać się z czasem."
    ),
    "Expected volatility (σ)": ("Oczekiwana zmienność (σ)"),
    "g_sigma": (
        "Prognoza GARCH odchylenia standardowego stopy zwrotu w kolejnych 20 sesjach. Pasmo "
        "σ na wykresie pokazuje, w jakim przedziale model widział kurs 20 sesji wcześniej."
    ),
    "Stress regime": ("Reżim stresu"),
    "g_regime": (
        "Prawdopodobieństwo, że rynek jest w stanie wysokiej zmienności, z modelu dwóch "
        "stanów dziennych stóp zwrotu WIG. Liczone tylko z sesji do danego dnia włącznie."
    ),
    "Breadth": ("Szerokość rynku"),
    "g_breadth": (
        "Ile spółek rynku głównego rośnie, a ile spada, ile jest powyżej swojej średniej z "
        "200 sesji i ile wyznacza nowe maksima lub minima 52-tygodniowe. Pokazuje, czy ruch "
        "WIG niesie większość spółek, czy kilka dużych."
    ),
    "Screener presets": ("Zestawy w skanerze"),
    "g_presets": (
        "Gotowe filtry, na przykład RSI poniżej 30 albo nowe maksimum. To nie są sygnały "
        "kupna ani sprzedaży: czy takie układy opłacały się na GPW po kosztach, sprawdza "
        "strona Sygnały, tą samą metodą co raporty."
    ),
    "Market": "Rynek",
    "Screener": "Skaner",
    "Reports": "Raporty",
    "More": "Więcej",
    "Main": "Główna nawigacja",
    "Find a stock": "Szukaj spółki",
    "GPW + NewConnect: charts, screener and a nightly report radar. Not investment advice.": (
        "GPW i NewConnect: wykresy, skaner i wieczorny radar raportów. To nie jest "
        "rekomendacja inwestycyjna."
    ),
    "Market · Undercurrent": "Rynek · Undercurrent",
    "Screener · Undercurrent": "Skaner · Undercurrent",
    "Reports · Undercurrent": "Raporty · Undercurrent",
    "Charts": "Wykresy",
    "Chart data could not be loaded.": "Nie udało się wczytać danych wykresu.",
    "Loading…": "Wczytywanie…",
    "The market tonight": "Rynek po sesji",
    "market_lede": (
        "WIG i jego reżim, szerokość rynku, największe ruchy i niespodzianki dnia. Wszystko liczone"
        " po zamknięciu sesji, na cenach skorygowanych o dywidendy i splity."
    ),
    "Indices": "Indeksy",
    "5 s.": "1 tydz.",
    "20 s.": "1 mies.",
    "60 s.": "3 mies.",
    "1 y.": "1 rok",
    "Stress regime: {p}": "Reżim stresu: {p}",
    "regime_title": (
        "Prawdopodobieństwo, że rynek jest w stanie wysokiej zmienności (model dwóch stanów "
        "dziennych stóp zwrotu WIG, tylko z sesji do dziś włącznie)."
    ),
    "WIG, the stress probability and the share of stocks above their 200-session average": (
        "WIG, prawdopodobieństwo stresu i odsetek spółek powyżej średniej z 200 sesji"
    ),
    "Breadth, main market": "Szerokość rynku, rynek główny",
    "Up / down today": "Wzrosty / spadki dziś",
    "Above their 200-session average": "Powyżej średniej z 200 sesji",
    "of": "z",
    "New 52-week highs / lows": "Nowe maksima / minima 52-tygodniowe",
    "Reports tonight": "Raporty dziś",
    "insider": "insiderzy",
    "inside info": "inf. poufne",
    "holdings": "stany posiadania",
    "Open the report feed": "Otwórz listę raportów",
    "Biggest rises": "Największe wzrosty",
    "Biggest falls": "Największe spadki",
    "Biggest surprises": "Największe niespodzianki",
    "movers_note": (
        "Tylko spółki, którymi da się handlować (mediana obrotu co najmniej 10 000 zł dziennie). "
        "Niespodzianka: ruch dnia ponad to, co tłumaczą WIG i obligacje, w odchyleniach "
        "standardowych prognozy dla tej spółki."
    ),
    "All {n} listed stocks": "Wszystkie notowane spółki: {n}",
    "stress probability": "prawdopodobieństwo stresu",
    "share above the 200-session average": "odsetek powyżej średniej z 200 sesji",
    "All stocks": "Wszystkie spółki",
    "New 52-week high": "Nowe maksimum 52-tygodniowe",
    "New 52-week low": "Nowe minimum 52-tygodniowe",
    "RSI below 30": "RSI poniżej 30",
    "RSI above 70": "RSI powyżej 70",
    "Above both averages": "Powyżej obu średnich",
    "Surprise beyond 3σ today": "Niespodzianka ponad 3σ dziś",
    "Insider buying, last 20 sessions": "Zakupy insiderów, ostatnie 20 sesji",
    "Model forecast, liquid WIG": "Prognoza modelu, płynne spółki WIG",
    "screener_lede": (
        "Wszystkie notowane spółki na dzisiejszym zamknięciu. Sortuj po dowolnej kolumnie, filtruj "
        "albo zacznij od gotowego zestawu. Stopy zwrotu liczone względem WIG, wskaźniki na cenach "
        "skorygowanych."
    ),
    "What the columns mean": "Co znaczą kolumny",
    "Preset": "Zestaw",
    "Both": "Oba",
    "All, thin included": "Wszystkie, także niepłynne",
    "Tradable (≥ 10,000 zł a day)": "Handlowalne (≥ 10 000 zł dziennie)",
    "Liquid (≥ 100,000 zł a day)": "Płynne (≥ 100 000 zł dziennie)",
    "Search": "Szukaj",
    "Ticker or company": "Ticker lub spółka",
    "Columns": "Kolumny",
    "Technical": "Techniczne",
    "Risk": "Ryzyko",
    "Model": "Model",
    "Fundamentals": "Fundamenty",
    "screener_note": (
        "To nie jest rekomendacja inwestycyjna. Zestaw to filtr, a nie sygnał: czy takie układy "
        "opłacały się na GPW, mierzy strona Sygnały. Spółki niepłynne są ukryte, chyba że je "
        "wybierzesz."
    ),
    "Stock": "Spółka",
    "Close": "Kurs",
    "vs SMA50": "wzgl. SMA50",
    "vs SMA200": "wzgl. SMA200",
    "vs 52w high": "wzgl. maks. 52 tyg.",
    "Surprise": "Niespodzianka",
    "Turnover": "Obrót",
    "Cost": "Koszt",
    "Forecast": "Prognoza",
    "Insiders 20 s.": "Insiderzy, 1 mies.",
    "EBIT/assets": "EBIT/aktywa",
    "Asset growth": "Wzrost aktywów",
    "Accruals": "Memoriały",
    "No stock matches.": "Żadna spółka nie pasuje.",
    "{n} more: narrow the filters to see them.": "Jeszcze {n}: zawęź filtry, żeby je zobaczyć.",
    "{n} of {t} listed stocks": "{n} z {t} notowanych spółek",
    "The screener data could not be loaded.": "Nie udało się wczytać danych skanera.",
    "t_ret": "Zmiana dzisiaj, skorygowana o dywidendy i splity.",
    "t_x": "Stopa zwrotu minus stopa WIG w ostatnich N sesjach.",
    "t_rsi": (
        "RSI Wildera z 14 sesji: powyżej 70 spółka ostatnio głównie rosła, poniżej 30 głównie "
        "spadała."
    ),
    "t_dma": "Kurs względem prostej średniej kroczącej.",
    "t_dhi": "Kurs względem najwyższego maksimum z ostatnich 250 sesji.",
    "t_atr": "Średni rzeczywisty zakres z 14 sesji jako ułamek ceny.",
    "t_s20": "Oczekiwana zmienność w kolejnych 20 sesjach (prognoza GARCH).",
    "t_beta": "Jak mocno spółka porusza się razem z WIG (filtr Kalmana, zmienia się w czasie).",
    "t_z": (
        "Ruch dnia ponad to, co tłumaczą WIG i obligacje, w odchyleniach standardowych prognozy."
    ),
    "t_turnover": "Mediana dziennego obrotu z ostatnich 60 sesji.",
    "t_cost": "Szacowany koszt kupna i sprzedaży za 10 000 zł: spread, prowizja i wpływ na cenę.",
    "t_fc": (
        "Oczekiwany wynik względem WIG w 20 sesjach według modelu spółki tygodnia i miejsce w "
        "rankingu (tylko płynne spółki WIG)."
    ),
    "t_insider": "Wartość zakupów insiderów minus sprzedaży zgłoszonych w ostatnich 20 sesjach.",
    "t_fund": (
        "Z ostatniego raportu rocznego (ESEF): stopa zysku, zysk operacyjny / aktywa, wzrost "
        "aktywów, memoriały."
    ),
    "Chart settings": "Ustawienia wykresu",
    "Indicators": "Wskaźniki",
    "Range": "Zakres",
    "On the price": "Na cenie",
    "band_title": (
        "Dla każdej sesji przedział ±1σ kursu, który model stanu rynku prognozował 20 sesji "
        "wcześniej. Mniej więcej dwa kursy na trzy powinny w nim leżeć."
    ),
    "σ band, forecast 20 sessions earlier": "pasmo σ, prognoza sprzed 20 sesji",
    "Adjusted for dividends and splits": "Skorygowane o dywidendy i splity",
    "Panes": "Panele",
    "Surprise (σ)": "Niespodzianka (σ)",
    "Beta to WIG": "Beta do WIG",
    "No session since {d}": "Brak sesji od {d}",
    "Liquid": "Płynna",
    "vs WIG, 20 s.": "wzgl. WIG, 1 mies.",
    "vs WIG, 1 y.": "wzgl. WIG, 1 rok",
    "vs WIG, 5 / 60 s.": "wzgl. WIG, 1 tydz. / 3 mies.",
    "Price chart of {t}": "Wykres kursu {t}",
    "insider selling": "sprzedaż insiderów",
    "chart_note": (
        "Kropki to raporty-sygnały; kliknij, aby otworzyć stronę raportu. Najedź na wykres, aby "
        "odczytać sesję."
    ),
    "Indicators tonight": "Wskaźniki na dziś",
    "vs SMA50 / SMA200": "wzgl. SMA50 / SMA200",
    "vs 52-week high / low": "wzgl. maks. / min. 52-tygodniowego",
    "new high": "nowe maksimum",
    "new low": "nowe minimum",
    "of the price, a typical day’s range": "ceny, typowy zakres dnia",
    "Not listed any more, or no recent session: the chart shows its history.": (
        "Nie jest już notowana albo dawno nie miała sesji: wykres pokazuje jej historię."
    ),
    "Risk, cost and the model": "Ryzyko, koszt i model",
    "Surprise today": "Niespodzianka dziś",
    "Round trip costs about": "Kupno i sprzedaż kosztują około",
    "rank {r} among liquid WIG stocks": "miejsce {r} wśród płynnych spółek WIG",
    "Insiders, last 20 sessions": "Insiderzy, ostatnie 20 sesji",
    "bought minus sold": "kupione minus sprzedane",
    "Last annual report (ESEF)": "Ostatni raport roczny (ESEF)",
    "asset growth": "wzrost aktywów",
    "accruals": "memoriały",
    "Reports of this company": "Raporty tej spółki",
    "No signal reports in the lake yet.": "Brak raportów-sygnałów w danych.",
    "Chart and indicators of {t}": "Wykres i wskaźniki {t}",
    "{t} around this report": "{t} wokół tego raportu",
    "Full chart and indicators": "Pełny wykres i wskaźniki",
    # corporate calendar (corp_calendar.py)
    "Calendar": "Kalendarz",
    "Calendar · Undercurrent": "Kalendarz · Undercurrent",
    "Ex-dividend": "Bez dywidendy",
    "Dividend paid": "Wypłata dywidendy",
    "Results": "Wyniki",
    "AGM": "ZWZ",
    "EGM": "NWZ",
    "General meeting": "Walne zgromadzenie",
    "Q1 {y}": "I kw. {y}",
    "Q2 {y}": "II kw. {y}",
    "Q3 {y}": "III kw. {y}",
    "Q4 {y}": "IV kw. {y}",
    "H1 {y}": "I półrocze {y}",
    "Annual report {y}": "Raport roczny za {y}",
    "approved": "uchwalona",
    "proposed": "rekomendacja",
    "interim": "zaliczka",
    "Next {n} days": "Najbliższe {n} dni",
    "calendar_lede": (
        "Dywidendy, wyniki i walne zgromadzenia na najbliższe tygodnie, odczytane z raportów "
        "bieżących spółek."
    ),
    "The calendar has not been built yet tonight.": "Kalendarz nie został jeszcze dziś zbudowany.",
    "Ex-dividend dates": "Dni bez dywidendy",
    "in the next {n} days": "w ciągu {n} dni",
    "Results due": "Zapowiedziane wyniki",
    "dates the companies announced": "terminy podane przez spółki",
    "General meetings": "Walne zgromadzenia",
    "{a} ordinary, {b} extraordinary": "zwyczajne: {a}, nadzwyczajne: {b}",
    "Filter the calendar": "Filtruj kalendarz",
    "Dividends": "Dywidendy",
    "Meetings": "Zgromadzenia",
    "amount not stated": "kwota nie podana",
    "record date {d}": "dzień dywidendy {d}",
    "Nothing announced for the coming weeks.": "Na najbliższe tygodnie nic nie zapowiedziano.",
    "Dividends ahead": "Nadchodzące dywidendy",
    "Per share": "Na akcję",
    "Yield": "Stopa",
    "Ex-date": "Bez prawa",
    "Paid": "Wypłata",
    "dividend_table_note": (
        "Stopa: kwota na akcję wobec ostatniego kursu. „Rekomendacja” to propozycja zarządu; "
        "decyduje walne zgromadzenie."
    ),
    "No dividend with an ex-date ahead.": "Brak dywidend z nadchodzącym dniem bez prawa.",
    "What the ex-date does": "Co robi kurs w dniu bez dywidendy",
    "drop_claim": "W dniu bez dywidendy kurs spadał o medianę <b>{m}</b> dywidendy.",
    "drop_explain": (
        "Po odjęciu ruchu WIG, na {n} wcześniejszych dywidendach wynoszących co najmniej 1% "
        "kursu, potwierdzonych przez giełdę (95% przedział {lo}–{hi}). Po 19% podatku "
        "dywidenda jest warta 81% kwoty brutto: kurs spada mniej więcej o tyle, ile dywidenda "
        "jest warta po podatku, a nie o całą kwotę."
    ),
    "Not enough past dividends in the data yet.": "Za mało wcześniejszych dywidend w danych.",
    "How far to trust the dates": "Na ile ufać terminom",
    "Dividends checked against the exchange": "Dywidendy zgodne z giełdą",
    "Announced report dates kept": "Dotrzymane terminy raportów",
    "within a day; {e} early": "co do dnia; wcześniej: {e}",
    "calendar_check_note": (
        "W dniu bez dywidendy giełda obniża kurs odniesienia o dywidendę; kwota odczytana z "
        "raportu jest zgodna, gdy odpowiada tej obniżce z dokładnością do ticku. Termin raportu "
        "jest dotrzymany, gdy raport okresowy ukazał się najwyżej dzień od niego; spółki mogą "
        "publikować wcześniej, a w danych są luki, więc to dolna granica."
    ),
    "How it’s read": "Jak to odczytujemy",
    # holders (ownership.py)
    "Shareholders": "Akcjonariusze",
    "Shareholders · Undercurrent": "Akcjonariusze · Undercurrent",
    "Holders of 5% or more": "Posiadacze co najmniej 5%",
    "owners_lede": (
        "Kto ma co najmniej 5% głosów i kto właśnie kupił lub sprzedał, z zawiadomień o "
        "przekroczeniu progów i list akcjonariuszy z walnych zgromadzeń."
    ),
    "The holder register has not been built yet tonight.": (
        "Rejestr akcjonariuszy nie został jeszcze dziś zbudowany."
    ),
    "Stake changes, last {n} days": "Zmiany udziałów, ostatnie {n} dni",
    "Major holders on record": "Znaczni akcjonariusze w rejestrze",
    "in {n} companies, stated in the last three years": "w {n} spółkach, z ostatnich trzech lat",
    "Notices that state the stake": "Zawiadomienia z podanym udziałem",
    "the rest only in an attachment": "reszta tylko w załączniku",
    "Who bought and who sold": "Kto kupował, kto sprzedawał",
    "threshold notices, newest first": "zawiadomienia o progach, od najnowszych",
    "Holder": "Akcjonariusz",
    "Before": "Przed",
    "After": "Po",
    "not named in the text": "nie wymieniony w treści",
    "up": "wzrost",
    "down": "spadek",
    "across {t}%": "próg {t}%",
    "No threshold notices in the last {n} days.": (
        "Brak zawiadomień o progach w ostatnich {n} dniach."
    ),
    "The largest holders across the market": "Najwięksi akcjonariusze na rynku",
    "holders with 5% or more in at least three companies": (
        "co najmniej 5% głosów w co najmniej trzech spółkach"
    ),
    "Not enough holders on record yet.": "Za mało akcjonariuszy w rejestrze.",
    "owners_note": (
        "Udział w ogólnej liczbie głosów z ostatniego zawiadomienia lub listy z walnego "
        "zgromadzenia ({lists} list). Akcjonariusz między progami nie musi się zgłaszać, więc "
        "udział może być starszy niż dzisiejszy. Kto zszedł poniżej 5%, wypada z rejestru."
    ),
    "Coming up": "Nadchodzące",
    "Major shareholders": "Znaczni akcjonariusze",
    "Votes": "Głosy",
    "As of": "Stan na",
    "5% or more": "5% lub więcej",
    "old": "stare",
    "No holder of 5% or more named in the reports of the last three years.": (
        "Raporty z ostatnich trzech lat nie wymieniają akcjonariusza z co najmniej 5% głosów."
    ),
    "{n} older records (over three years)": "Starsze wpisy (ponad trzy lata): {n}",
    "holders_table_note": (
        "Udział w ogólnej liczbie głosów, z ostatniego zawiadomienia lub listy z walnego "
        "zgromadzenia, które wymieniają akcjonariusza."
    ),
    "No holder of 5% or more named in the reports.": (
        "Raporty nie wymieniają akcjonariusza z co najmniej 5% głosów."
    ),
    # how it works: calendar and holders
    "Shareholders and calendar": "Akcjonariusze i kalendarz",
    "Dividends and their dates": "Dywidendy i ich terminy",
    "Checked against the exchange": "Sprawdzenie z giełdą",
    "Price on the ex-date": "Kurs w dniu bez dywidendy",
    "Report dates and meetings": "Terminy raportów i walne zgromadzenia",
    "Threshold notices": "Zawiadomienia o progach",
    "General-meeting lists": "Listy z walnych zgromadzeń",
    "Matching holders": "Łączenie akcjonariuszy",
    "g_dividends": (
        "Kwotę na akcję, dzień dywidendy i termin wypłaty odczytujemy regułami z raportów "
        "bieżących: formularza giełdy „Decyzja o wypłacie dywidendy”, rekomendacji zarządu i "
        "uchwały walnego zgromadzenia. Najnowszy raport o danej dywidendzie zastępuje "
        "wcześniejsze. Dywidenda spółki zależnej dla spółki matki nie jest dywidendą emitenta i "
        "jest pomijana. Dzień bez dywidendy to sesja przed dniem dywidendy (rozliczenie T+2)."
    ),
    "g_dividend_check": (
        "Każdą przeszłą dywidendę sprawdzamy z giełdą: w dniu bez dywidendy kurs odniesienia "
        "spada o dywidendę, więc różnica między poprzednim zamknięciem a kursem odniesienia "
        "odtwarza kwotę, którą przyjął rynek. Zgodność do ticku liczymy jako trafienie. "
        "Rozbieżności to najczęściej błąd w raporcie (gr zamiast zł), dywidenda, której walne "
        "nie uchwaliło, albo luka w danych."
    ),
    "g_drop": (
        "Współczynnik spadku to (poprzednie zamknięcie − zamknięcie − ruch WIG) ÷ dywidenda. "
        "Tylko dywidendy potwierdzone przez giełdę i wynoszące co najmniej 1% kursu, gdzie "
        "zaokrąglenie do ticku nie zagłusza wyniku. Przedział 95% mediany z bootstrapu."
    ),
    "g_report_dates": (
        "Na początku roku spółki podają terminy raportów kwartalnych, półrocznych i rocznych; "
        "późniejsze raporty przesuwają pojedyncze daty. Raport, który ukazał się wcześniej, "
        "znika z kalendarza. Walne zgromadzenia pochodzą z ogłoszeń o zwołaniu; odwołane są "
        "pomijane."
    ),
    "g_notices": (
        "Akcjonariusz, który przekroczy 5, 10, 15, 20, 25, 33, 50, 75 lub 90% głosów, musi "
        "zawiadomić spółkę, a ta publikuje raport. Odczytujemy akcjonariusza, udział przed i po "
        "oraz kierunek. Wiele spółek tylko załącza pismo akcjonariusza w PDF, który giełda "
        "udostępnia za ochroną przed botami; wtedy w treści jest często tylko nazwa, a udział "
        "zostaje pusty."
    ),
    "g_lists": (
        "Po każdym walnym zgromadzeniu spółka publikuje listę akcjonariuszy z co najmniej 5% "
        "głosów na zgromadzeniu, zwykle z udziałem w ogólnej liczbie głosów. Udział na "
        "zgromadzeniu nigdy nie jest mniejszy niż w ogólnej liczbie głosów, więc z dwóch "
        "procentów w wierszu mniejszy to udział w ogólnej liczbie."
    ),
    "g_holder_match": (
        "Ten sam akcjonariusz w różnych raportach dostaje ten sam klucz: pierwsze pięć liter "
        "każdego słowa nazwy, bez form prawnych, co przetrwa odmianę („Pawła Knopika” to „Paweł "
        "Knopik”). Duże fundusze emerytalne i inwestycyjne łączymy po nazwie grupy."
    ),
}

EN_OVERRIDES: dict[str, str] = {
    "pitch_h1": "The Warsaw market after the close, in one place.",
    "pitch_sub": (
        "Charts, a screener and tonight's ESPI reports for every GPW and NewConnect stock. "
        "Every evening after the session, free."
    ),
    "pitch_meta": (
        "Charts, a screener and tonight's ESPI reports for every GPW and NewConnect stock, "
        "every evening after the session. No recommendations, just data."
    ),
    "rrg_caption": "Sectors on the right beat WIG; at the top their lead is growing.",
    "screener_lede_short": "Every listed stock. Sort, filter or start from a preset.",
    "reports_lede_short": "Every ESPI and EBI report from tonight, newest first.",
    "movers_lede": (
        "Stocks that moved beyond ±2σ against the market today, and everything published "
        "about them since the previous close: filings and headlines, newest first. This is "
        "not an explanation of the move."
    ),
    "signals_lede_short": (
        "Which kinds of report move the price on the day, and which still pay after the "
        "digest, after costs."
    ),
    "reaction_note": (
        "The move against the market from the report's day to the first session a reader of "
        "the digest can buy at, in the stock's own usual volatility, compared with days without "
        "any report. Moves go up and down, so their average is about zero; what counts is "
        "their size. The table below measures something else: whether there is anything left "
        "to earn after the digest, net of costs."
    ),
    "setups_lede_short": "Every screener preset, tested on GPW's history.",
    "archive_lede_short": "Earlier digests and how their stocks have done since.",
    "record_note_short": "Result against WIG from entry to today.",
    "glossary_lede_short": (
        "Short definitions of the site's terms. Hover over an underlined term to see it in place."
    ),
    "how_lede": "How we compute what the site shows: sources, methods and limits.",
    "xtb_short": "Undercurrent is not affiliated with XTB.",
    "event_chart_note_plain": (
        "An event's chart shows the stock's average result against WIG from 5 sessions "
        "before to 20 sessions after day 0, with a 95% band. The figures below it run from "
        "entry (the close after the digest), the first moment a reader could buy."
    ),
    "forms_how": (
        "When a copy of the MAR notification form is on parkiet.com, bankier.pl or "
        "biznesradar.pl, the fields come from it, and the report's text fills in what it "
        "leaves out."
    ),
    "logos_how": (
        "A logo is XTB's logo for the stock's exchange code or the icon from the company's "
        "own website, kept and served by Undercurrent. It is the company's trademark, shown "
        "only to identify the stock."
    ),
    "5 s.": "1 wk",
    "20 s.": "1 mo",
    "60 s.": "3 mo",
    "1 y.": "1 yr",
    "vs WIG, 20 s.": "vs WIG, 1 month",
    "vs WIG, 1 y.": "vs WIG, 1 year",
    "vs WIG, 5 / 60 s.": "vs WIG, 1 week / 3 months",
    "Insiders 20 s.": "Insiders, 1 mo",
    "Forecast vs WIG, 20 s.": "Forecast vs WIG, 1 mo",
    "Insiders, 20 s.": "Insiders, 1 mo",
    "no_recent_report": (
        "No annual report from the last 18 months in the data: the ESEF repository it comes "
        "from has no Polish reports added after July 2024. Shares, earnings and book value come "
        "from today's GPW table."
    ),
    "val_note": (
        "Yields rather than multiples: a loss is a low yield, not a negative P/E. Percentile "
        "100 is the cheapest of the group. The value score is the mean percentile of the "
        "earnings yield, book to price and free-cash-flow yield. No EV/EBITDA: bank debt is "
        "not tagged uniformly in ESEF reports."
    ),
    "dcf_note": (
        "Reverse DCF: the yearly growth of free cash flow over 10 years (then 2.5% a year) "
        "at which its present value equals the market cap. Equity-level, without a debt "
        "adjustment. This is what the market assumes, not a forecast."
    ),
    "f_roa_positive": "return on assets positive",
    "f_cfo_positive": "operating cash flow positive",
    "f_roa_up": "return on assets up",
    "f_cash_above_profit": "cash flow above profit",
    "f_leverage_down": "long-term liabilities to assets down",
    "f_liquidity_up": "current ratio up",
    "f_no_new_shares": "no new shares",
    "f_margin_up": "gross margin up",
    "f_turnover_up": "asset turnover up",
    "z_safe": "safe zone",
    "z_grey": "grey zone",
    "z_distress": "distress zone",
    "t_mcap": "Shares from GPW's indicators table times today's close.",
    "t_pe": "At today's close: earnings from GPW's indicators table, book value and dividend.",
    "t_fcfy": (
        "Operating cash flow minus capital expenditure from the last annual report, over "
        "market cap."
    ),
    "t_value": "Mean sector percentile of E/P, B/P and FCF yield; 100% is the cheapest.",
    "t_fscore": (
        "Piotroski's F-score: how many of nine quality tests the report passes (when at "
        "least eight can be judged)."
    ),
    "t_altman": "Altman's Z: below 1.81 distress zone, above 2.99 safe; not for financials.",
    "g_valuation": (
        "Market cap is the number of shares from GPW's daily indicators table times the "
        "close. Earnings yield (E/P), book to price (B/P), free-cash-flow yield (operating "
        "cash flow minus purchases of fixed and intangible assets, over market cap) and EBIT "
        "yield are the inverses of multiples, so a loss is simply a low yield. Each is "
        "compared with the stocks of the same GPW sector (at least six with a value), "
        "otherwise with its sector group, otherwise with all listed stocks."
    ),
    "g_dcf": (
        "Instead of guessing growth and deriving a value from it, a reverse DCF asks what "
        "yearly growth of free cash flow over ten years, then 2.5% a year, justifies today's "
        "market cap at discount rates of 8, 10 and 12%. A high number means the market "
        "expects a lot."
    ),
    "g_fscore": (
        "Nine simple tests of the annual report against the year before: profitability, cash "
        "flow, cash flow above profit, leverage (here: long-term liabilities), liquidity, "
        "share issues, margin and asset turnover. The page says how many tests the report "
        "allows."
    ),
    "g_altman": (
        "Altman's Z combines working capital, retained earnings, EBIT and revenue (each over "
        "assets) with market cap over liabilities. Below 1.81, bankruptcies were "
        "historically more frequent. It is not computed for banks, insurers and funds."
    ),
    "t_rvol": "Today's turnover against the mean of the 20 sessions before.",
    "t_adx": (
        "Wilder's ADX over 14 sessions: the strength of a trend whichever way it goes; above "
        "25 the trend is clear."
    ),
    "t_var": (
        "Value at risk over one session: a loss the price should not exceed on 95% of sessions."
    ),
    "t_es": "The mean loss on the worst 2.5% of sessions (expected shortfall).",
    "t_dd": "Close against its highest since the last hole in the data.",
    "t_volpct": "The share of the last year's sessions with a lower σ than tonight.",
    "risk_note": (
        "From the σ forecast for the next session (GARCH with the day's range) and a "
        "Student-t with unit variance, fitted to the stock's history. The backtest uses the "
        "σ known the evening before and a ν fitted before the year it tests. Kupiec's test: "
        "p below 0.05 means the number of breaches does not fit the model. Not investment "
        "advice."
    ),
    "rrg_note": (
        "Right: the sector has been beating WIG lately (its ratio to WIG above its "
        "50-session mean). Up: that lead is growing (against ten sessions earlier). The tail "
        "is the last four weeks; sectors usually turn clockwise. This describes; it does "
        "not forecast."
    ),
    "g_adx": (
        "ADX 14 (Wilder) measures how strong a trend is, whichever way: the smoothed rises "
        "of the highs and falls of the lows against the true range. Above 25 the trend is "
        "clear. Relative turnover is today's turnover against the mean of the 20 sessions "
        "before; 1.5 or more is clearly more trading than usual. Bollinger width is (upper − "
        "lower) / middle; its lowest in half a year marks a volatility squeeze."
    ),
    "g_var": (
        "VaR 95% is the loss over one session that the price should not exceed on 95 "
        "sessions in 100; expected shortfall at 97.5% is the mean loss on the worst 2.5% of "
        "sessions. Both come from tomorrow's σ forecast and a Student-t scaled to unit "
        "variance, because a plain t has a standard deviation above 1 and would overstate "
        "the risk. The backtest compares the last year's breaches with the expected number "
        "(Kupiec's test)."
    ),
    "g_rotation": (
        "A sector index divided by WIG is its relative strength. The trend is that strength "
        "against its 50-session mean, the momentum the trend's change over 10 sessions; both "
        "are 100 when nothing changes. Leading: both above 100; weakening: trend above, "
        "momentum below; lagging: both below; improving: trend below, momentum above."
    ),
    "setups_lede": (
        "Each screener preset tested like the reports: the event is the session a stock "
        "enters the preset's state (RSI falling below 30, say). Entry at the next session's "
        "close, trading costs, tradable stocks only, the same correction for many tests and "
        "the same test on new data."
    ),
    "setups_footnote": (
        "A stock counts again only after 20 sessions. Indicators on adjusted prices, "
        "restarting after a hole in the data. The setups are their own family of tests: they "
        "do not change the reports' statistics."
    ),
    "setup_line": (
        "Stocks entering this preset ({n} cases): {mean} against WIG over 20 sessions on "
        "average; on new data after costs {oos} a trade. Verdict: {verdict}."
    ),
    "g_indicators": (
        "Computed on prices adjusted for dividends and splits, each restarting after a hole "
        "in the data. SMA n: the mean of the last n closes. RSI 14 (Wilder): average gain "
        "against average loss over 14 sessions, from 0 to 100; above 70 the stock has mostly "
        "risen lately, below 30 mostly fallen. MACD 12, 26, 9: the difference of the 12- and "
        "26-session exponential averages, its 9-session average and the histogram of the "
        "difference. Bollinger 20, 2: SMA 20 ± 2 standard deviations. ATR 14: the average "
        "true range of a session. 52-week high and low: over the last 250 sessions. They "
        "describe the past; they are not forecasts."
    ),
    "g_surprise": (
        "The day's move that WIG and bonds (through the stock's betas) do not explain, "
        "divided by the standard deviation forecast the evening before. 3 or more is rare: "
        "something happened."
    ),
    "g_beta": (
        "By how many percent the price usually moves when WIG moves 1%. Estimated with a "
        "Kalman filter, so it can change over time."
    ),
    "g_sigma": (
        "The GARCH forecast of the standard deviation of the next 20 sessions' return. The σ "
        "band on the chart shows the range the model expected for the close 20 sessions "
        "earlier."
    ),
    "g_regime": (
        "The probability that the market is in its high-volatility state, from a two-state "
        "model of WIG's daily returns. Uses only sessions up to that day."
    ),
    "g_breadth": (
        "How many main-market stocks rise and fall, how many are above their 200-session "
        "average, and how many set new 52-week highs or lows. It shows whether WIG's move is "
        "carried by most stocks or by a few large ones."
    ),
    "g_presets": (
        "Ready-made filters such as RSI below 30 or a new high. They are not buy or sell "
        "signals: whether such setups have paid off on GPW after costs is tested on the "
        "Signals page, the same way as the reports."
    ),
    "market_lede": (
        "WIG and its regime, the market's breadth, the day's biggest moves and surprises. All "
        "measured after the session's close, on prices adjusted for dividends and splits."
    ),
    "regime_title": (
        "The filtered probability that the market is in its high-volatility state (a two-state "
        "model of WIG's daily returns, using only sessions up to tonight)."
    ),
    "movers_note": (
        "Tradable stocks only (median turnover at least 10,000 zł a day). Surprise: the day's move "
        "beyond what WIG and bonds explain, in standard deviations of the stock's own forecast."
    ),
    "screener_lede": (
        "Every listed stock at tonight's close. Sort by any column, filter, or start from a preset."
        " Returns are against WIG; indicators are on adjusted prices."
    ),
    "screener_note": (
        "Not investment advice. A preset is a filter, not a signal: whether such setups have paid "
        "off on GPW is measured on the Signals page. Thin stocks are hidden unless you ask for "
        "them."
    ),
    "t_ret": "Today's change, adjusted for dividends and splits.",
    "t_x": "Return minus WIG's over the last N sessions.",
    "t_rsi": (
        "Wilder's RSI over 14 sessions: above 70 the stock has mostly risen lately, below 30 mostly"
        " fallen."
    ),
    "t_dma": "Close against its simple moving average.",
    "t_dhi": "Close against the highest high of the last 250 sessions.",
    "t_atr": "Average true range over 14 sessions, as a share of the price.",
    "t_s20": "Expected volatility of the next 20 sessions (GARCH forecast).",
    "t_beta": "How much the stock moves with WIG (Kalman filter, it changes over time).",
    "t_z": (
        "The day's move beyond what WIG and bonds explain, in standard deviations of its forecast."
    ),
    "t_turnover": "Median daily turnover over the last 60 sessions.",
    "t_cost": (
        "Estimated round-trip cost of a 10,000 zł order: spread, commission and market impact."
    ),
    "t_fc": (
        "The stock-of-the-week model's expected result against WIG over 20 sessions, and its rank "
        "(liquid WIG stocks only)."
    ),
    "t_insider": "Value of insider purchases minus sales reported in the last 20 sessions.",
    "t_fund": (
        "From the last annual report (ESEF): earnings yield, operating profit / assets, asset "
        "growth, accruals."
    ),
    "band_title": (
        "For each session, the ±1σ range of the close that the market state forecast 20 sessions "
        "earlier. About two closes in three should fall inside."
    ),
    "chart_note": (
        "Dots mark signal reports; click one to open its page. Move over the chart to read a "
        "session."
    ),
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
        "sessions. Once a week the basket buys the 15 stocks with the best forecast and keeps "
        "each until it drops out of the best 45; the stock of the week is the one with the "
        "best forecast. Below is everything "
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
        "Baskets recorded when they were made, never corrected; beside each week, its best "
        "forecast. Only this result really counts."
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
    "calendar_lede": (
        "Dividends, results and general meetings of the coming weeks, read from the companies' "
        "own current reports."
    ),
    "dividend_table_note": (
        "Yield: the amount per share against the last close. “Proposed” is the board’s "
        "recommendation; the general meeting decides."
    ),
    "drop_claim": "On the ex-date the price fell by a median <b>{m}</b> of the dividend.",
    "drop_explain": (
        "Market move taken out, over {n} past dividends of at least 1% of the price that the "
        "exchange confirmed (95% interval {lo}–{hi}). After Poland's 19% tax a dividend is "
        "worth 81% of its gross amount: the price falls by about what the dividend is worth "
        "after tax, not by all of it."
    ),
    "calendar_check_note": (
        "On the ex-date the exchange lowers the reference price by the dividend; an amount read "
        "from a report agrees when it matches that cut to within a tick. A report date counts as "
        "kept when a periodic report came out within a day of it; companies may publish earlier "
        "and the lake has holes, so this is a floor."
    ),
    "owners_lede": (
        "Who holds 5% or more of the votes, and who has just bought or sold, from threshold "
        "notices and general-meeting lists."
    ),
    "owners_note": (
        "Share of all votes from the latest notice or general-meeting list ({lists} lists). A "
        "holder between thresholds need not report, so a stake can be older than today. A "
        "holder who fell below 5% leaves the register."
    ),
    "holders_table_note": (
        "Share of all votes, from the last threshold notice or general-meeting list that names "
        "the holder."
    ),
    "g_dividends": (
        "The amount per share, the record date and the payment date are read by rules from "
        "current reports: the exchange's dividend form, the board's recommendation and the "
        "general meeting's resolution. The latest report about a dividend replaces earlier ones. "
        "A subsidiary paying its parent is not the issuer's dividend and is skipped. The "
        "ex-date is the session before the record date (T+2 settlement)."
    ),
    "g_dividend_check": (
        "Every past dividend is checked against the exchange: on the ex-date the reference price "
        "drops by the dividend, so the previous close minus the reference price recovers the "
        "amount the market used. A match to within a tick counts as agreeing. Mismatches are "
        "mostly errors in the report (grosze for złoty), a dividend the meeting did not approve, "
        "or a hole in the data."
    ),
    "g_drop": (
        "The drop-off ratio is (previous close − close − the WIG move) ÷ dividend. Only "
        "dividends the exchange confirmed and of at least 1% of the price, where tick rounding "
        "cannot swamp the result. A bootstrap 95% interval of the median."
    ),
    "g_report_dates": (
        "Early in the year companies announce the dates of their quarterly, half-year and "
        "annual reports; later reports move single dates. A report that came out early drops "
        "off the calendar. General meetings come from the notices convening them; cancelled "
        "ones are left out."
    ),
    "g_notices": (
        "A holder who crosses 5, 10, 15, 20, 25, 33, 50, 75 or 90% of the votes must notify the "
        "company, which publishes a report. We read the holder, the stake before and after, and "
        "the direction. Many companies only attach the holder's letter as a PDF, which the "
        "exchange serves behind bot protection; then the text often names only the holder and "
        "the stake stays empty."
    ),
    "g_lists": (
        "After each general meeting the company lists the holders with at least 5% of the votes "
        "at the meeting, usually with their share of all votes. A share at the meeting is never "
        "below the share of all votes, so of a row's two percentages the smaller is the share of "
        "all votes."
    ),
    "g_holder_match": (
        "The same holder in different reports gets the same key: the first five letters of each "
        "word of the name, legal forms removed, which survives Polish declension (“Pawła "
        "Knopika” is “Paweł Knopik”). Large pension and investment funds are matched by their "
        "group's name."
    ),
}

# Polish plural forms: (1, 2–4, 5+), keyed by the English singular.
PL_PLURALS: dict[str, tuple[str, str, str]] = {
    "report": ("raport", "raporty", "raportów"),
    "item": ("pozycja", "pozycje", "pozycji"),
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
    "company": ("spółka", "spółki", "spółek"),
}
EN_PLURALS: dict[str, str] = {
    "report": "reports",
    "item": "items",
    "event of a kind that has moved prices": "events of kinds that have moved prices",
    "event worth a look today": "events worth a look today",
    "session": "sessions",
    "company": "companies",
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
        "setup:new_high": "New 52-week high",
        "setup:new_low": "New 52-week low",
        "setup:rsi_below_30": "RSI falls below 30",
        "setup:rsi_above_70": "RSI rises above 70",
        "setup:above_both_averages": "Close moves above SMA50 and SMA200",
        "setup:golden_cross": "SMA50 crosses above SMA200 (golden cross)",
        "setup:pullback": "Pullback in an uptrend",
        "setup:squeeze": "Volatility squeeze",
        "setup:breakout": "Breakout on heavy turnover",
        "setup:oversold": "Oversold near the 52-week low",
        "setup:trend_health": "Healthy trend (ADX above 25)",
        "setup:cheap_vs_sector": "Cheap against its sector",
        "setup:quality": "Strong annual report (F-score 7+)",
        "setup:distress": "Altman distress zone",
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
        "setup:new_high": "Nowe maksimum 52-tygodniowe",
        "setup:new_low": "Nowe minimum 52-tygodniowe",
        "setup:rsi_below_30": "RSI spada poniżej 30",
        "setup:rsi_above_70": "RSI rośnie powyżej 70",
        "setup:above_both_averages": "Kurs przechodzi nad SMA50 i SMA200",
        "setup:golden_cross": "SMA50 przecina SMA200 od dołu (złoty krzyż)",
        "setup:pullback": "Korekta w trendzie wzrostowym",
        "setup:squeeze": "Ściśnięcie zmienności",
        "setup:breakout": "Wybicie przy dużym obrocie",
        "setup:oversold": "Wyprzedanie blisko minimum 52-tygodniowego",
        "setup:trend_health": "Zdrowy trend (ADX powyżej 25)",
        "setup:cheap_vs_sector": "Tania na tle sektora",
        "setup:quality": "Mocny raport roczny (F-score 7+)",
        "setup:distress": "Strefa zagrożenia Altmana",
    },
}
BASIS_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "insider_tx": "Art. 19 MAR",
        "holder_threshold": "Art. 69 POA",
        "inside_info": "Art. 17 MAR",
        "setup": "Chart setup",
    },
    "pl": {
        "insider_tx": "Art. 19 MAR",
        "holder_threshold": "Art. 69 ustawy o ofercie",
        "inside_info": "Art. 17 MAR",
        "setup": "Układ na wykresie",
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
        "earn_drift": "Reaction to its latest results (in standard deviations, 3 months)",
        "abn_turnover": "Turnover last week against the 3 months before",
        "earnings_yield": "Earnings yield (EPS / price, last annual report)",
        "profitability": "Operating profit / assets (last annual report)",
        "asset_growth": "Asset growth (last annual report)",
        "accruals": "Accruals: profit not backed by cash, / assets (last annual report)",
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
        "earn_drift": "Reakcja na ostatnie wyniki (w odchyleniach standardowych, 3 miesiące)",
        "abn_turnover": "Obrót w ostatnim tygodniu wobec 3 miesięcy wcześniej",
        "earnings_yield": "Stopa zysku (EPS / cena, ostatni raport roczny)",
        "profitability": "Zysk operacyjny / aktywa (ostatni raport roczny)",
        "asset_growth": "Wzrost aktywów (ostatni raport roczny)",
        "accruals": "Memoriały: zysk bez pokrycia w gotówce / aktywa (ostatni raport roczny)",
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
            "after costs. This stock is the basket's best forecast, not a proven winner."
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
            "pokonywał WIG po kosztach. Ta spółka to najlepsza prognoza koszyka, a nie "
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
    """``772800`` → ``773k`` (en) / ``773 tys.`` (pl); ``5.7e10`` → ``56.9bn`` / ``56,9 mld``."""
    if x is None:
        return "—"
    if abs(x) >= 1e9:
        return f"{x / 1e9:.1f}bn" if lang != "pl" else f"{x / 1e9:.1f} mld".replace(".", ",")
    if abs(x) >= 1e6:
        return f"{x / 1e6:.1f}m" if lang != "pl" else f"{x / 1e6:.1f} mln".replace(".", ",")
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k" if lang != "pl" else f"{x / 1e3:.0f} tys."
    return f"{x:.0f}"
