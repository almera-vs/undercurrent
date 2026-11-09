"""MAR Art. 19 form parser. Texts mirror real pdfplumber output; names are fictional."""

from datetime import date

from undercurrent.forms import parse_forms, side_of, summarise, venue_of

STANDARD_PL = """Powiadomienie o transakcji/transakcjach*, o którym mowa
w art. 19 ust. 1 rozporządzenia MAR
1 Dane osoby pełniącej obowiązki zarządcze / osoby blisko z nią związanej
a) Nazwa/Nazwisko Tadeusz Nowak
2 Powód powiadomienia
a) Stanowisko/status Członek Rady Nadzorczej
b) Powiadomienie pierwotne / zmiana Powiadomienie pierwotne
3 Dane emitenta, uczestnika rynku uprawnień do emisji, platformy aukcyjnej, prowadzącego
aukcje lub monitorującego aukcje
a) Nazwa PRZYKŁAD SPÓŁKA AKCYJNA
b) LEI 259400ZQ2L60G9NEO560
4 Szczegółowe informacje dotyczące transakcji: rubrykę tę należy wypełnić dla (i) każdego
rodzaju instrumentu; (ii) każdego rodzaju transakcji; (iii) każdej daty; oraz (iv) każdego
miejsca, w którym przeprowadzono transakcje
a) Opis instrumentu finansowego, akcje
rodzaj instrumentu PLPRZYK00012
Kod identyfikacyjny
b) Rodzaj transakcji Nabycie
c) Cena i wolumen Cena Wolumen
100,6 PLN 100
100,8 PLN 100
d) Informacje zbiorcze
- Łączny wolumen 200
- Cena 100,7 PLN
e) Data transakcji 2026-10-15
f) Miejsce transakcji XWAR – GPW rynek akcji
a) Opis instrumentu finansowego, akcje
rodzaj instrumentu PLPRZYK00012
Kod identyfikacyjny
b) Rodzaj transakcji Nabycie
c) Cena i wolumen Cena Wolumen
101,4 PLN 100
101,6 PLN 100
101,8 PLN 52
Strona 1 z 2
d) Informacje zbiorcze
- Łączny wolumen 252
- Cena 101,56 PLN
e) Data transakcji 2026-10-16
f) Miejsce transakcji XWAR – GPW rynek akcji
* Niepotrzebne skreślić
Strona 2 z 2
"""

# Long cells wrap: values sit before and after their labels.
REFLOWED_PL = """Powiadomienie o transakcji/transakcjach*, o którym mowa
w art. 19 ust. 1 rozporządzenia MAR
1 Dane osoby pełniącej obowiązki zarządcze/osoby blisko z nią związanej
a) Nazwa/Nazwisko Joao Pedro Da Silva Santos
2 Powód powiadomienia
Wiceprezes Zarządu Przykład S.A. (osoba pełniąca
a) Stanowisko/status
obowiązki zarządcze)
b) Pierwotne powiadomienie/zmiana Pierwotne powiadomienie
Powód zmiany**: nie dotyczy
Dane emitenta, uczestnika rynku uprawnień do emisji, platformy aukcyjnej, prowadzącego
3
aukcje lub monitorującego aukcje
a) Nazwa Przykład S.A.
b) LEI 2594001577BLP3DX2J37
Szczegółowe informacje dotyczące transakcji: rubrykę tę należy wypełnić dla (i) każdego
4 rodzaju instrumentu; (ii) każdego rodzaju transakcji; (iii) każdej daty; oraz (iv) każdego
miejsca, w którym przeprowadzono transakcje
Opis instrumentu finansowego, rodzaj
Akcje zwykłe na okaziciela Przykład S.A.
instrumentu
a)
ISIN: PLPRZYK00012
Kod identyfikacyjny
Zbycie akcji w transakcjach sesyjnych na rynku
b) Rodzaj transakcji
regulowanym
Cena Wolumen
c) Cena i wolumen 5,88 PLN 1 180
5,90 PLN 146
Informacje zbiorcze
d) Łączny wolumen 1 326 akcji
Cena 5,8822 PLN (średnia ważona)
e) Data transakcji 2026-10-22
Giełda Papierów Wartościowych w Warszawie S.A.,
f) Miejsce transakcji
rynek regulowany (XWAR)
* Niepotrzebne skreślić
** Wypełnić jeżeli dotyczy
Data sporządzenia: 22.10.2026
"""

ENGLISH = """Notification on transaction/transactions* referred to
in Article 19(1) of MAR
1 Data of the person discharging managerial responsibilities / person closely associated with such
person
a) Entity name/Last name Example Holdings Limited
2 Reason for submitting the notification
a) Job title/status Person closely associated with persons discharging managerial
responsibilities
b) Original notification / change Original notification
3 Data of the issuer, emission allowance market participant, auction platform, auctioneer or
auction monitor
a) Entity name EXAMPLE, INC.
b) LEI 254900U4ZTZOIOBB4181
4 Details of the transaction: please fill in this field for (i) each instrument type; (ii) each
transaction type; (iii) each date; and (iv) each trading venue
a) Description of the financial Shares - EXM.XWAR.EQT
instrument, instrument type US44853H1086
Identification code
b) Transaction type Acquisition
c) Price and volume Price Volume
24,5 PLN 5681
d) Aggregate information
- Total volume 5681
- Price 24,5 PLN
e) Transaction date 2026-10-16
f) Trading venue XWAR – Warsaw Stock Exchange/Equities/Main Market
* Delete as appropriate
Page 1 of 1
"""


def _pledge(name: str, amendment: str, nature: str) -> str:
    return f"""Powiadomienie o transakcji/transakcjach*, o którym mowa
w art. 19 ust. 1 rozporządzenia MAR
1 Dane osoby pełniącej obowiązki zarządcze / osoby blisko z nią związanej
a) Nazwa/Nazwisko {name}
2 Powód powiadomienia
a) Stanowisko/status Prezes Zarządu
b) Powiadomienie pierwotne / zmiana {amendment}
3 Dane emitenta
a) Nazwa PRZYKŁAD SE
b) LEI 2594004XCEJP2AO2DF56
4 Szczegółowe informacje dotyczące transakcji
a) Opis instrumentu finansowego, AKCJE
rodzaj instrumentu PLPRZYK00012
Kod identyfikacyjny
b) Rodzaj transakcji {nature}
c) Cena i wolumen Cena Wolumen
0,50 PLN 1000000
d) Informacje zbiorcze
- Łączny wolumen 1000000
- Cena 0,50 PLN
e) Data transakcji 2026-10-28
f) Miejsce transakcji POZA SYSTEMEM OBROTU
"""


def test_standard_form_two_blocks() -> None:
    (f,) = parse_forms(STANDARD_PL)
    assert f.lang == "pl" and f.person == "Tadeusz Nowak"
    assert f.position == "Członek Rady Nadzorczej" and f.amendment is False
    assert f.issuer == "PRZYKŁAD SPÓŁKA AKCYJNA" and f.lei == "259400ZQ2L60G9NEO560"
    assert len(f.transactions) == 2
    t0, t1 = f.transactions
    assert (t0.isin, t0.side, t0.volume, t0.price) == ("PLPRZYK00012", "buy", 200, 100.7)
    assert t0.rows == [(100.6, 100), (100.8, 100)] and t0.currency == "PLN"
    assert t1.trade_date == date(2026, 10, 16) and t1.venue == "GPW" and t1.volume == 252
    s = summarise([f], "PLPRZYK00012")
    assert s["form_side"] == "buy" and s["form_volume"] == 452
    assert abs(s["form_avg_price"] - (200 * 100.7 + 252 * 101.56) / 452) < 1e-9
    assert s["form_trade_date"] == date(2026, 10, 15)


def test_reflowed_table_cells() -> None:
    (f,) = parse_forms(REFLOWED_PL)
    assert f.person == "Joao Pedro Da Silva Santos"
    assert f.position == "Wiceprezes Zarządu Przykład S.A. (osoba pełniąca obowiązki zarządcze)"
    (t,) = f.transactions
    assert t.isin == "PLPRZYK00012" and t.side == "sell"
    assert t.rows == [(5.88, 1180), (5.90, 146)]
    assert t.volume == 1326 and t.price == 5.8822
    assert t.trade_date == date(2026, 10, 22) and t.venue == "GPW"


def test_english_variant() -> None:
    (f,) = parse_forms(ENGLISH)
    assert f.lang == "en" and f.person == "Example Holdings Limited"
    (t,) = f.transactions
    assert (t.side, t.volume, t.price, t.isin) == ("buy", 5681, 24.5, "US44853H1086")
    assert t.trade_date == date(2026, 10, 16) and t.venue == "GPW"


def test_polish_form_wins_over_translation_and_corrections_replace_originals() -> None:
    pl = parse_forms(STANDARD_PL)
    en = parse_forms(ENGLISH)
    assert summarise(pl + en, None)["form_person"] == "Tadeusz Nowak"
    forms = parse_forms(
        _pledge("Jan Kowalski", "Powiadomienie pierwotne", "Zbycie")
        + _pledge("Example Capital S.A.", "zmiana", "Zbycie")
        + _pledge("Example Capital S.A.", "zmiana", "Nabycie")
    )
    assert len(forms) == 3
    s = summarise(forms, "PLPRZYK00012")
    assert s["form_side"] == "buy" and s["form_person"] == "Example Capital S.A."
    assert s["form_venue"] == "off_market" and s["form_amendment"] is True


def test_non_share_instruments_do_not_drive_the_summary() -> None:
    (f,) = parse_forms(_pledge("Jan Kowalski", "Powiadomienie pierwotne", "Ustanowienie zastawu"))
    assert summarise([f], "PLPRZYK00012")["form_side"] == "pledge"
    assert summarise([f], "PLOTHER000019")["form_side"] is None  # other issuer's ISIN


def test_not_a_form() -> None:
    assert parse_forms("Uchwała Zarządu w sprawie zwołania walnego zgromadzenia") == []


def test_side_and_venue_mapping() -> None:
    assert side_of("Nabycie akcji") == "buy" and side_of("Sale") == "sell"
    assert side_of("Ustanowienie zastawu rejestrowego") == "pledge"
    assert side_of("Darowizna akcji") == "gift"
    assert side_of("Objęcie warrantów subskrypcyjnych") == "incentive"
    assert side_of(None) is None and side_of("Inna czynność") is None
    assert venue_of("XWAR – GPW rynek akcji") == "GPW"
    assert venue_of("poza systemem obrotu umowa cywilnoprawna") == "off_market"
    assert venue_of("WDER – GPW rynek derywatów") == "GPW_derivatives"
    assert venue_of("XNCO NewConnect") == "NewConnect"
    assert venue_of("Warszawa, ul. Przykładowa 1") == "other"
