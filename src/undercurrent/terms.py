"""Short definitions of the site's technical terms, shown when a reader hovers over one.

Each entry says only what the term means, in a sentence or two. How Undercurrent measures
it belongs on the "How it works" page (``how.html``), not here. ``publish`` renders a term
with the ``term(key)`` template function; ``glossary.html`` lists them all.
"""

from __future__ import annotations

# key: (Polish label, Polish definition, English label, English definition)
TERMS: dict[str, tuple[str, str, str, str]] = {
    # returns and the market
    "excess": (
        "Względem WIG",
        "Wynik akcji minus wynik indeksu WIG w tym samym czasie. Plus oznacza, że spółka "
        "radziła sobie lepiej niż rynek.",
        "Versus WIG",
        "The stock's return minus the WIG index's over the same period. A plus means the "
        "stock did better than the market.",
    ),
    "session": (
        "Sesja",
        "Jeden dzień notowań. 5 sesji to mniej więcej tydzień, 20 miesiąc, 60 kwartał, 250 rok.",
        "Session",
        "One trading day. 5 sessions are about a week, 20 a month, 60 a quarter, 250 a year.",
    ),
    "regime": (
        "Reżim stresu",
        "Prawdopodobieństwo, że rynek jest w okresie podwyższonej zmienności.",
        "Stress regime",
        "The probability that the market is in a period of high volatility.",
    ),
    "breadth": (
        "Szerokość rynku",
        "Ile spółek rośnie, a ile spada. Mówi, czy ruch indeksu niesie większość rynku, "
        "czy kilka dużych spółek.",
        "Breadth",
        "How many stocks rise and how many fall: whether a move of the index is carried by "
        "most of the market or by a few large stocks.",
    ),
    "phase_leading": (
        "Prowadzi",
        "Sektor jest lepszy od WIG, a jego przewaga rośnie.",
        "Leading",
        "The sector beats WIG and its lead is growing.",
    ),
    "phase_weakening": (
        "Słabnie",
        "Sektor jest lepszy od WIG, ale jego przewaga maleje.",
        "Weakening",
        "The sector beats WIG, but its lead is shrinking.",
    ),
    "phase_lagging": (
        "Odstaje",
        "Sektor jest słabszy od WIG i wciąż traci.",
        "Lagging",
        "The sector trails WIG and keeps losing ground.",
    ),
    "phase_improving": (
        "Poprawia się",
        "Sektor jest słabszy od WIG, ale odrabia straty.",
        "Improving",
        "The sector trails WIG but is catching up.",
    ),
    # risk
    "sigma": (
        "σ",
        "Zmienność: typowa wielkość dziennego ruchu kursu (odchylenie standardowe stóp "
        "zwrotu), w procentach.",
        "σ",
        "Volatility: the typical size of a day's price move (the standard deviation of "
        "returns), in per cent.",
    ),
    "sigma20": (
        "σ 20",
        "Oczekiwana zmienność kursu w ciągu najbliższych 20 sesji, w procentach.",
        "σ 20",
        "The expected volatility of the price over the next 20 sessions, in per cent.",
    ),
    "vol_pct": (
        "Percentyl σ",
        "Jaka część sesji z ostatniego roku miała niższą zmienność niż dziś. 90% to "
        "wyjątkowo nerwowy okres.",
        "σ percentile",
        "The share of the last year's sessions with lower volatility than today. 90% means "
        "an unusually nervous time.",
    ),
    "beta": (
        "β",
        "Beta: o ile procent zwykle zmienia się kurs, gdy WIG zmienia się o 1%. Powyżej 1 "
        "spółka porusza się mocniej niż rynek, poniżej 1 słabiej.",
        "β",
        "Beta: how many per cent the price usually moves when WIG moves 1%. Above 1 the "
        "stock moves more than the market, below 1 less.",
    ),
    "surprise": (
        "Niespodzianka",
        "Ruch kursu, którego nie tłumaczy rynek, w odchyleniach standardowych (σ). Ponad "
        "3σ zdarza się rzadko.",
        "Surprise",
        "The part of the price move the market does not explain, in standard deviations "
        "(σ). Beyond 3σ is rare.",
    ),
    "var": (
        "VaR",
        "Wartość zagrożona: strata na jednej sesji, której kurs nie powinien przekroczyć w "
        "95 (albo 99) sesjach na 100.",
        "VaR",
        "Value at risk: the one-session loss the price should not exceed on 95 (or 99) "
        "sessions out of 100.",
    ),
    "es": (
        "ES 97,5",
        "Oczekiwana strata w złym dniu: średnia strata z 2,5% najgorszych sesji.",
        "ES 97.5",
        "Expected shortfall: the average loss on the worst 2.5% of sessions.",
    ),
    "var_check": (
        "Test VaR",
        "Czy w ostatnim roku dni ze stratą większą niż VaR zdarzały się mniej więcej tak "
        "często, jak powinny.",
        "VaR check",
        "Whether, over the last year, losses beyond the VaR happened about as often as they "
        "should.",
    ),
    "drawdown": (
        "Spadek od szczytu",
        "O ile procent kurs jest poniżej swojego najwyższego poziomu.",
        "Drawdown",
        "How far, in per cent, the price is below its highest level.",
    ),
    "band": (
        "Pasmo σ",
        "Przedział, w którym kurs powinien leżeć mniej więcej dwa razy na trzy według "
        "prognozy zmienności.",
        "σ band",
        "The range the price should stay within about two times out of three, by the "
        "volatility forecast.",
    ),
    "band90": (
        "Pasmo 90%",
        "Zakres, w który wynik powinien wpaść 9 razy na 10.",
        "90% band",
        "The range the result should fall into 9 times out of 10.",
    ),
    "bad_case": (
        "Zły scenariusz (1 na 20)",
        "Wynik, od którego gorzej wypadał tylko 1 przypadek na 20.",
        "Bad case (1 in 20)",
        "The result that only 1 case in 20 did worse than.",
    ),
    # trading
    "turnover": (
        "Obrót",
        "Wartość akcji, które zmieniły właściciela na sesji, w złotych.",
        "Turnover",
        "The value of the shares that changed hands in a session, in złoty.",
    ),
    "rvol": (
        "Obrót względny",
        "Dzisiejszy obrót w stosunku do zwykłego obrotu spółki. 2× to dwa razy więcej niż zwykle.",
        "Relative turnover",
        "Today's turnover against the stock's usual turnover. 2× is twice the usual.",
    ),
    "cost": (
        "Koszt",
        "Szacowany koszt kupna i sprzedaży razem: prowizje i różnica między ceną kupna a "
        "sprzedaży, w procentach.",
        "Cost",
        "The estimated cost of buying and selling together: commissions and the gap between "
        "the bid and the ask, in per cent.",
    ),
    "liquidity": (
        "Płynność",
        "Jak łatwo kupić lub sprzedać akcje bez ruszania kursu. Handlowalna: co najmniej "
        "10 tys. zł obrotu dziennie, płynna: 100 tys. zł.",
        "Liquidity",
        "How easily the shares can be bought or sold without moving the price. Tradable: at "
        "least 10,000 zł a day; liquid: 100,000 zł.",
    ),
    "thin": (
        "Zbyt mało płynna",
        "Kurs poniżej 0,50 zł albo obrót poniżej 10 tys. zł dziennie: trudno kupić lub "
        "sprzedać bez ruszania kursu.",
        "Too thin to trade",
        "A price below 0.50 zł or turnover below 10,000 zł a day: hard to buy or sell "
        "without moving the price.",
    ),
    "insiders": (
        "Insiderzy",
        "Członkowie zarządu i rady nadzorczej oraz osoby z nimi związane. Muszą ogłaszać "
        "swoje transakcje akcjami spółki.",
        "Insiders",
        "Board members and people closely associated with them. They must disclose their "
        "trades in the company's shares.",
    ),
    "day0": (
        "Dzień 0",
        "Sesja, na której raport po raz pierwszy mógł wpłynąć na kurs.",
        "Day 0",
        "The session on which the report could first move the price.",
    ),
    # chart indicators
    "rsi": (
        "RSI",
        "Wskaźnik siły względnej (0–100) z 14 sesji: porównuje wzrosty ze spadkami. Poniżej "
        "30 mówi się o wyprzedaniu, powyżej 70 o wykupieniu.",
        "RSI",
        "Relative strength index (0–100) over 14 sessions: rises against falls. Below 30 is "
        "called oversold, above 70 overbought.",
    ),
    "sma": (
        "SMA",
        "Średnia krocząca: przeciętny kurs zamknięcia z ostatnich N sesji, np. 50 albo 200.",
        "SMA",
        "Simple moving average: the average close over the last N sessions, e.g. 50 or 200.",
    ),
    "macd": (
        "MACD",
        "Różnica średnich wykładniczych z 12 i 26 sesji oraz jej średnia z 9 sesji. Pokazuje, "
        "czy trend przyspiesza, czy słabnie.",
        "MACD",
        "The gap between 12- and 26-session exponential averages, with its own 9-session "
        "average. Shows whether a trend speeds up or fades.",
    ),
    "bollinger": (
        "Wstęga Bollingera",
        "Pasmo wokół średniej z 20 sesji, szerokie na dwa odchylenia standardowe kursu.",
        "Bollinger band",
        "A band around the 20-session average, two standard deviations of the price wide.",
    ),
    "atr": (
        "ATR",
        "Średni prawdziwy zakres: typowa rozpiętość kursu w ciągu sesji, tu w procentach ceny.",
        "ATR",
        "Average true range: the typical spread of the price within a session, here in per "
        "cent of the price.",
    ),
    "adx": (
        "ADX",
        "Siła trendu (0–100), bez względu na jego kierunek. Powyżej 25 trend uznaje się za "
        "wyraźny.",
        "ADX",
        "The strength of a trend (0–100), whatever its direction. Above 25 a trend counts as "
        "clear.",
    ),
    "hi52": (
        "Szczyt 52 tygodni",
        "Najwyższy kurs z ostatniego roku.",
        "52-week high",
        "The highest price of the last year.",
    ),
    # valuation
    "mcap": (
        "Kapitalizacja",
        "Wartość rynkowa spółki: liczba akcji razy kurs.",
        "Market cap",
        "The company's market value: the number of shares times the price.",
    ),
    "pe": (
        "C/Z",
        "Cena do zysku: kapitalizacja podzielona przez roczny zysk netto.",
        "P/E",
        "Price to earnings: market cap divided by the yearly net profit.",
    ),
    "pbv": (
        "C/WK",
        "Cena do wartości księgowej: kapitalizacja podzielona przez kapitał własny.",
        "P/BV",
        "Price to book: market cap divided by shareholders' equity.",
    ),
    "ep": (
        "Stopa zysku",
        "Roczny zysk netto podzielony przez kapitalizację; odwrotność C/Z.",
        "Earnings yield",
        "Yearly net profit divided by market cap; the inverse of P/E.",
    ),
    "bp": (
        "WK/C",
        "Wartość księgowa do ceny: kapitał własny podzielony przez kapitalizację.",
        "B/P",
        "Book to price: shareholders' equity divided by market cap.",
    ),
    "dy": (
        "Stopa dywidendy",
        "Roczna dywidenda na akcję podzielona przez kurs.",
        "Dividend yield",
        "The yearly dividend per share divided by the price.",
    ),
    "ex_date": (
        "Dzień bez dywidendy",
        "Pierwsza sesja, na której akcję kupuje się już bez prawa do najbliższej dywidendy. "
        "Przypada sesję przed dniem dywidendy, bo transakcja rozlicza się dwa dni robocze.",
        "Ex-dividend date",
        "The first session on which a share is bought without the right to the coming "
        "dividend: the session before the record date, since a trade settles two business "
        "days later.",
    ),
    "fcfy": (
        "Stopa FCF",
        "Wolne przepływy pieniężne (gotówka z działalności minus inwestycje) podzielone "
        "przez kapitalizację.",
        "FCF yield",
        "Free cash flow (cash from operations minus investment) divided by market cap.",
    ),
    "ebit_yield": (
        "Stopa EBIT",
        "Zysk operacyjny podzielony przez kapitalizację.",
        "EBIT yield",
        "Operating profit divided by market cap.",
    ),
    "ebit_assets": (
        "EBIT/aktywa",
        "Zysk operacyjny podzielony przez aktywa: jak dochodowy jest majątek spółki.",
        "EBIT/assets",
        "Operating profit divided by total assets: how profitable the company's assets are.",
    ),
    "asset_growth": (
        "Wzrost aktywów",
        "Roczna zmiana sumy aktywów spółki.",
        "Asset growth",
        "The yearly change in the company's total assets.",
    ),
    "accruals": (
        "Memoriały",
        "Zysk księgowy minus gotówka z działalności, do aktywów. Wysoka wartość to zysk "
        "słabo pokryty gotówką.",
        "Accruals",
        "Accounting profit minus operating cash flow, over assets. A high value is profit "
        "with little cash behind it.",
    ),
    "percentile": (
        "Percentyl",
        "Miejsce w grupie w skali 0–100: 80 to wynik lepszy niż u 80% spółek z grupy.",
        "Percentile",
        "A place in the group on a 0–100 scale: 80 beats 80% of the group.",
    ),
    "value_rank": (
        "Ranking wartości",
        "Jak tania jest spółka na tle podobnych, z zysku, wartości księgowej i przepływów: "
        "100 to najtańsza w grupie.",
        "Value rank",
        "How cheap the stock is against similar ones, by earnings, book value and cash "
        "flow: 100 is the cheapest of the group.",
    ),
    "dcf": (
        "Odwrócony DCF",
        "Roczny wzrost wolnych przepływów, który musiałby nastąpić, by dzisiejsza cena była "
        "uczciwa.",
        "Reverse DCF",
        "The yearly growth of free cash flow that would make today's price fair.",
    ),
    "fscore": (
        "F-score",
        "Ocena Piotroskiego (0–9): ile z dziewięciu prostych testów kondycji finansowej "
        "spółka zdaje w raporcie rocznym.",
        "F-score",
        "Piotroski's score (0–9): how many of nine simple tests of financial health the "
        "annual report passes.",
    ),
    "altman": (
        "Z Altmana",
        "Miara ryzyka upadłości z pięciu proporcji bilansu i wyników. Poniżej 1,81 strefa "
        "zagrożenia, powyżej 2,99 bezpieczna.",
        "Altman Z",
        "A measure of bankruptcy risk from five balance-sheet and earnings ratios. Below "
        "1.81 is the distress zone, above 2.99 safe.",
    ),
    # statistics
    "q": (
        "q (FDR)",
        "Szansa, że wynik to przypadek, gdy sprawdza się wiele rzeczy naraz. Poniżej 0,05 "
        "uznaje się wynik za istotny.",
        "q (FDR)",
        "The chance a result is luck when many things are tested at once. Below 0.05 counts "
        "as significant.",
    ),
    "ci95": (
        "95% CI",
        "Przedział ufności: zakres, w którym z 95-procentową pewnością leży prawdziwa średnia.",
        "95% CI",
        "Confidence interval: the range that holds the true average with 95% confidence.",
    ),
    "hit_rate": (
        "Trafność",
        "Odsetek przypadków, w których akcja wypadła lepiej niż WIG.",
        "Hit rate",
        "The share of cases in which the stock did better than WIG.",
    ),
    "walk_forward": (
        "Test na nowych danych",
        "Regułę ustala się na wcześniejszych latach i sprawdza na kolejnych, których nie "
        "widziała (walk-forward).",
        "Walk-forward",
        "A rule is chosen on earlier years and tried on later years it has not seen.",
    ),
    "deflated_sr": (
        "Deflowany Sharpe",
        "Wskaźnik Sharpe'a (zysk do ryzyka) skorygowany o szczęście przy sprawdzaniu wielu "
        "wariantów. Powyżej 0,95 przekonuje.",
        "Deflated Sharpe",
        "The Sharpe ratio (return per unit of risk) corrected for luck when many variants "
        "are tried. Above 0.95 is convincing.",
    ),
    "ic": (
        "IC",
        "Korelacja rang: jak dobrze ranking przewiduje kolejność późniejszych wyników. 0 to "
        "brak związku.",
        "IC",
        "Rank correlation: how well a ranking predicts the order of later results. 0 means "
        "no link.",
    ),
    "forecast": (
        "Prognoza",
        "Oczekiwany wynik akcji względem WIG w ciągu 20 sesji według modelu. Szacunek, nie "
        "obietnica.",
        "Forecast",
        "The model's expected result of the stock against WIG over 20 sessions. An "
        "estimate, not a promise.",
    ),
    "basket": (
        "Koszyk",
        "15 spółek z najlepszą prognozą modelu, po równo; spółka zostaje, dopóki nie wypadnie "
        "z najlepszych 45.",
        "Basket",
        "The 15 stocks with the model's best forecast, in equal parts; a stock stays until it "
        "drops out of the best 45.",
    ),
    "out_of_sample": (
        "Poza próbą",
        "Wynik na danych, których model nie widział, gdy go budowano.",
        "Out of sample",
        "A result on data the model had not seen when it was built.",
    ),
}


def label(key: str, lang: str) -> str:
    """The term's short label in ``lang``."""
    pl_label, _, en_label, _ = TERMS[key]
    return pl_label if lang == "pl" else en_label


def definition(key: str, lang: str) -> str:
    """The term's definition in ``lang``."""
    _, pl_def, _, en_def = TERMS[key]
    return pl_def if lang == "pl" else en_def


def glossary(lang: str) -> list[tuple[str, str, str]]:
    """``(key, label, definition)`` for every term, alphabetical in ``lang``."""
    rows = [(k, label(k, lang), definition(k, lang)) for k in TERMS]
    return sorted(rows, key=lambda r: _fold(r[1]))


def _fold(s: str) -> str:
    """Sort key ignoring case and Polish diacritics, symbols last."""
    table = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    t = s.translate(table).lower()
    return t if t[:1].isalnum() else "~" + t


# term → its section on the "How it works" page (how.html), where Undercurrent's method is
HOW: dict[str, str] = {
    "excess": "excess",
    "session": "session",
    "regime": "regime",
    "breadth": "breadth",
    "phase_leading": "rotation",
    "phase_weakening": "rotation",
    "phase_lagging": "rotation",
    "phase_improving": "rotation",
    "sigma": "sigma",
    "sigma20": "sigma",
    "band": "sigma",
    "vol_pct": "var",
    "beta": "beta",
    "surprise": "surprise",
    "var": "var",
    "es": "var",
    "var_check": "var",
    "drawdown": "var",
    "bad_case": "bad-case",
    "turnover": "liquidity",
    "cost": "costs",
    "liquidity": "liquidity",
    "thin": "liquidity",
    "insiders": "insider-fields",
    "day0": "day0",
    "rsi": "indicators",
    "sma": "indicators",
    "macd": "indicators",
    "bollinger": "indicators",
    "atr": "indicators",
    "hi52": "indicators",
    "adx": "adx",
    "rvol": "adx",
    "mcap": "valuation",
    "pe": "valuation",
    "pbv": "valuation",
    "ep": "valuation",
    "bp": "valuation",
    "dy": "valuation",
    "fcfy": "valuation",
    "ex_date": "ex-date",
    "ebit_yield": "valuation",
    "percentile": "valuation",
    "value_rank": "valuation",
    "dcf": "dcf",
    "fscore": "fscore",
    "altman": "altman",
    "q": "q",
    "ci95": "study",
    "hit_rate": "study",
    "walk_forward": "walk-forward",
    "deflated_sr": "study",
    "ic": "ridge",
    "forecast": "model",
    "basket": "model",
    "out_of_sample": "backtest",
    "band90": "backtest",
}
